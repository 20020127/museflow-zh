#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MuseFlow 视频剪辑与拼接工具 (Video Editor & Stitcher)
==================================================
使用本地 FFmpeg 9.0.2 引擎，支持：
1. 多分镜片段快速无损拼接 (Lossless Concat)
2. 电影级平滑交叉溶解转场 (Crossfade / xfade)
3. 自动混入背景音乐 (BGM) 与音量平衡淡入淡出
4. 画面规格统一 (1080p, 24fps/30fps, yuv420p)
"""

import os
import sys
import argparse
import subprocess
import json

def get_ffmpeg_path():
    # 优先使用系统 PATH 中的 ffmpeg
    for path in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(path, "ffmpeg.exe")
        if os.path.exists(candidate):
            return candidate
    return "ffmpeg"

def get_ffprobe_path():
    for path in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(path, "ffprobe.exe")
        if os.path.exists(candidate):
            return candidate
    return "ffprobe"

def probe_video(file_path):
    ffprobe = get_ffprobe_path()
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,width,height,duration,r_frame_rate",
        "-show_entries", "format=duration",
        "-of", "json", file_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(res.stdout)
        video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
        duration = float(data.get("format", {}).get("duration", 0) or (video_stream.get("duration", 0) if video_stream else 0))
        return {
            "path": file_path,
            "width": int(video_stream.get("width", 1280)) if video_stream else 1280,
            "height": int(video_stream.get("height", 720)) if video_stream else 720,
            "duration": duration,
            "has_audio": audio_stream is not None,
            "video_codec": video_stream.get("codec_name", "") if video_stream else ""
        }
    except Exception as e:
        print(f"[Warn] 探测视频信息失败 {file_path}: {e}")
        return {"path": file_path, "width": 1280, "height": 720, "duration": 5.0, "has_audio": False}

def stitch_videos_fast(video_files, output_path):
    """快速无损拼接 (所有分镜编码与分辨率一致时)"""
    ffmpeg = get_ffmpeg_path()
    list_file = os.path.join(os.path.dirname(output_path), "concat_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for v in video_files:
            abs_v = os.path.abspath(v).replace("\\", "/")
            f.write(f"file '{abs_v}'\n")
    
    cmd = [
        ffmpeg, "-y", "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path
    ]
    print(f"[FFmpeg] 正在执行快速无损拼接 -> {output_path}")
    subprocess.run(cmd, check=True)
    if os.path.exists(list_file):
        os.remove(list_file)
    print(f"[Success] 完整视频已生成: {output_path}")

def stitch_videos_cinematic(video_files, output_path, transition="fade", trans_dur=0.5, bgm_file=None, bgm_vol=0.3):
    """电影级交叉溶解拼接，支持统一规格与背景音乐"""
    ffmpeg = get_ffmpeg_path()
    probes = [probe_video(f) for f in video_files]
    n = len(probes)
    if n == 0:
        print("[Error] 没有可拼接的视频文件")
        return
    if n == 1:
        # 单个文件直接复制
        stitch_videos_fast(video_files, output_path)
        return

    width = probes[0]["width"]
    height = probes[0]["height"]

    # 构建滤镜链
    inputs = []
    filter_parts = []
    
    # 标准化各个输入视频
    for i, p in enumerate(probes):
        inputs.extend(["-i", p["path"]])
        v_dur = p["duration"]
        filter_parts.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps=24,setsar=1,format=yuv420p[v{i}]"
        )
        if not p["has_audio"]:
            filter_parts.append(f"aevalsrc=0:d={v_dur}:s=48000:c=stereo[a{i}]")
        else:
            filter_parts.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}]")

    # 交叉转场拼接
    last_v = "v0"
    last_a = "a0"
    accum_offset = probes[0]["duration"] - trans_dur

    for i in range(1, n):
        out_v = f"xv{i}"
        out_a = f"xa{i}"
        filter_parts.append(
            f"[{last_v}][v{i}]xfade=transition={transition}:duration={trans_dur}:offset={accum_offset:.3f}[{out_v}]"
        )
        filter_parts.append(
            f"[{last_a}][a{i}]acrossfade=d={trans_dur}[{out_a}]"
        )
        last_v = out_v
        last_a = out_a
        accum_offset += (probes[i]["duration"] - trans_dur)

    final_v = last_v
    final_a = last_a

    # 如果有背景音乐，混流进去
    if bgm_file and os.path.exists(bgm_file):
        bgm_idx = len(probes)
        inputs.extend(["-i", bgm_file])
        total_duration = accum_offset + trans_dur
        filter_parts.append(
            f"[{bgm_idx}:a]volume={bgm_vol},atrim=0:{total_duration:.3f},afade=t=in:st=0:d=1,afade=t=out:st={total_duration-1.5:.3f}:d=1.5[bgm]"
        )
        filter_parts.append(
            f"[{last_a}][bgm]amix=inputs=2:duration=first:dropout_transition=2[final_a]"
        )
        final_a = "final_a"

    cmd = [
        ffmpeg, "-y",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{final_v}]",
        "-map", f"[{final_a}]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        output_path
    ]

    print(f"[FFmpeg] 正在进行电影级交叉转场编码 ({transition}, 时长 {trans_dur}s) -> {output_path}")
    subprocess.run(cmd, check=True)
    print(f"[Success] 完整电影级视频生成成功: {output_path}")

def main():
    parser = argparse.ArgumentParser(description="MuseFlow 智能视频剪辑与拼接工具")
    subparsers = parser.add_subparsers(dest="command")

    # list
    p_list = subparsers.add_parser("list", help="查看指定目录中的视频文件")
    p_list.add_argument("--dir", default="assets_library/videos", help="视频目录")

    # stitch
    p_stitch = subparsers.add_parser("stitch", help="拼接多个视频片段为完整成片")
    p_stitch.add_argument("videos", nargs="*", help="视频文件列表 (留空则自动扫描目录)")
    p_stitch.add_argument("--dir", default="assets_library/videos", help="自动扫描目录")
    p_stitch.add_argument("-o", "--output", default="assets_library/videos/complete_movie.mp4", help="输出完整视频文件路径")
    p_stitch.add_argument("-m", "--mode", choices=["cinematic", "fast"], default="cinematic", help="拼接模式: cinematic (带平滑溶解转场) 或 fast (极速无损)")
    p_stitch.add_argument("-t", "--transition", default="fade", help="转场特效: fade, dissolve, wipeleft, wiperight, slideup, slidedown")
    p_stitch.add_argument("--trans-dur", type=float, default=0.5, help="转场过渡时长 (秒)")
    p_stitch.add_argument("--bgm", help="可选背景音乐文件路径 (mp3/wav)")
    p_stitch.add_argument("--bgm-vol", type=float, default=0.25, help="背景音乐音量 (0.0 - 1.0)")

    args = parser.parse_args()

    if args.command == "list":
        if not os.path.exists(args.dir):
            print(f"目录不存在: {args.dir}")
            return
        files = [os.path.join(args.dir, f) for f in os.listdir(args.dir) if f.lower().endswith(('.mp4', '.webm', '.mov'))]
        if not files:
            print(f"目录 {args.dir} 中没有视频文件")
            return
        print(f"共发现 {len(files)} 个视频文件:")
        for f in files:
            info = probe_video(f)
            print(f" - {os.path.basename(f)} | {info['width']}x{info['height']} | 时长: {info['duration']:.2f}s | 编码: {info['video_codec']}")
    elif args.command == "stitch" or not args.command:
        v_files = getattr(args, "videos", [])
        if not v_files:
            v_dir = getattr(args, "dir", "assets_library/videos")
            if os.path.exists(v_dir):
                v_files = sorted([os.path.join(v_dir, f) for f in os.listdir(v_dir) if f.lower().endswith(('.mp4', '.webm', '.mov'))])
        if not v_files:
            print("[Info] 未指定视频文件，且默认目录 assets_library/videos 为空。")
            print("用法提示:")
            print("  python video_editor.py stitch scene1.mp4 scene2.mp4 scene3.mp4 -o complete.mp4")
            print("  python video_editor.py stitch --mode cinematic --transition dissolve --bgm music.mp3")
            return
        out = getattr(args, "output", "assets_library/videos/complete_movie.mp4")
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        mode = getattr(args, "mode", "cinematic")
        if mode == "fast":
            stitch_videos_fast(v_files, out)
        else:
            stitch_videos_cinematic(
                v_files, out,
                transition=getattr(args, "transition", "fade"),
                trans_dur=getattr(args, "trans_dur", 0.5),
                bgm_file=getattr(args, "bgm", None),
                bgm_vol=getattr(args, "bgm_vol", 0.25)
            )

if __name__ == "__main__":
    main()
