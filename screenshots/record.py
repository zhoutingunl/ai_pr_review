"""录制一次评审的 Web UI 全过程（含流式输出动效）为视频。

用法: TASK_ID=13 python3.11 screenshots/record.py
输出: screenshots/video/*.webm（Playwright 原生），随后由外部 ffmpeg 转 mp4。
"""
import os
import time

from playwright.sync_api import sync_playwright

TASK_ID = os.environ.get("TASK_ID", "13")
URL = f"http://127.0.0.1:38001/task/{TASK_ID}"
DEADLINE = time.time() + 900  # 最长录 15 分钟

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        record_video_dir="screenshots/video",
        record_video_size={"width": 1280, "height": 900},
    )
    page = context.new_page()
    page.goto(URL, wait_until="networkidle", timeout=20000)

    # 任务页运行中有 #live-card；任务终态后页面自动 reload，该卡片消失
    terminal = False
    while time.time() < DEADLINE:
        try:
            has_live = page.locator("#live-card").count() > 0
        except Exception:
            has_live = True
        if not has_live:
            terminal = True
            break
        page.wait_for_timeout(3000)

    # 终态后多录几秒，把"流式卡片 -> 最终报告"的切换也收进去
    page.wait_for_timeout(4000)
    video_path = page.video.path()
    context.close()
    browser.close()
    print(f"terminal={terminal}")
    print(f"VIDEO={video_path}")
