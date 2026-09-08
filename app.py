import re
import json
import subprocess
from urllib.parse import urlparse

import instaloader
import yt_dlp

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


app = FastAPI(
    title="Social Scraper API",
    version="1.0.0"
)


MAX_COMMENTS = 30


class ScrapeRequest(BaseModel):
    url: str
    max_comments: int = MAX_COMMENTS


def detect_platform(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower()

    if hostname.startswith("www."):
        hostname = hostname[4:]

    if hostname in ["instagram.com", "instagr.am"]:
        return "instagram"

    if hostname in ["tiktok.com", "vm.tiktok.com", "vt.tiktok.com"]:
        return "tiktok"

    if hostname in ["youtube.com", "youtu.be", "m.youtube.com"]:
        return "youtube"

    if hostname in ["twitter.com", "x.com"]:
        return "twitter"

    if hostname in ["facebook.com", "fb.watch", "m.facebook.com"]:
        return "facebook"

    return "generic"


def extract_instagram_shortcode(url: str):
    patterns = [
        r"/p/([^/?#]+)",
        r"/reel/([^/?#]+)",
        r"/tv/([^/?#]+)"
    ]

    for pattern in patterns:
        match = re.search(pattern, url)

        if match:
            return match.group(1)

    return None


def scrape_instagram(url: str, max_comments: int):
    shortcode = extract_instagram_shortcode(url)

    if not shortcode:
        raise Exception(
            "Nao foi possivel identificar o shortcode do Instagram."
        )

    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_comments=False,
        save_metadata=False
    )

    post = instaloader.Post.from_shortcode(
        loader.context,
        shortcode
    )

    media = []

    try:
        if post.typename == "GraphSidecar":
            for node in post.get_sidecar_nodes():
                media.append(
                    node.video_url
                    if node.is_video
                    else node.display_url
                )
        else:
            media.append(
                post.video_url
                if post.is_video
                else post.url
            )
    except Exception:
        pass

    return {
        "success": True,
        "platform": "instagram",
        "source": "instaloader",
        "url": url,
        "author": post.owner_username,
        "description": post.caption,
        "date": post.date_utc.isoformat(),
        "likes": post.likes,
        "comments_count": post.comments,
        "comments": [],
        "comments_accessible": False,
        "media": media
    }


def scrape_ytdlp(url: str, platform: str):
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(
            url,
            download=False
        )

    media = []

    if info.get("thumbnail"):
        media.append(info["thumbnail"])

    return {
        "success": True,
        "platform": platform,
        "source": "yt-dlp",
        "url": url,
        "author": (
            info.get("uploader")
            or info.get("channel")
            or info.get("creator")
        ),
        "description": (
            info.get("description")
            or info.get("title")
        ),
        "title": info.get("title"),
        "date": info.get("upload_date"),
        "likes": info.get("like_count"),
        "comments_count": info.get("comment_count"),
        "comments": [],
        "comments_accessible": False,
        "media": media
    }


def scrape_gallery_dl(url: str, platform: str):
    process = subprocess.run(
        [
            "gallery-dl",
            "--dump-json",
            url
        ],
        capture_output=True,
        text=True,
        timeout=45
    )

    if process.returncode != 0:
        raise Exception(
            process.stderr.strip()
            or "gallery-dl falhou."
        )

    items = []

    for line in process.stdout.splitlines():
        if not line.strip():
            continue

        try:
            items.append(json.loads(line))
        except Exception:
            pass

    if not items:
        raise Exception(
            "gallery-dl nao retornou dados."
        )

    description = None
    author = None
    media = []

    def walk(value):
        nonlocal description
        nonlocal author

        if isinstance(value, dict):

            if not description:
                description = (
                    value.get("description")
                    or value.get("content")
                    or value.get("caption")
                    or value.get("text")
                    or value.get("title")
                )

            if not author:
                author = (
                    value.get("username")
                    or value.get("user")
                    or value.get("author")
                )

            possible_url = value.get("url")

            if (
                isinstance(possible_url, str)
                and possible_url.startswith("http")
            ):
                media.append(possible_url)

            for child in value.values():
                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(items)

    return {
        "success": True,
        "platform": platform,
        "source": "gallery-dl",
        "url": url,
        "author": author,
        "description": description,
        "comments": [],
        "comments_accessible": False,
        "media": list(dict.fromkeys(media))[:10]
    }


def scrape_with_fallback(url: str, platform: str, max_comments: int):
    errors = []

    if platform == "instagram":

        try:
            return scrape_instagram(
                url,
                max_comments
            )
        except Exception as error:
            errors.append(
                "Instaloader: " + str(error)
            )

        try:
            return scrape_ytdlp(
                url,
                platform
            )
        except Exception as error:
            errors.append(
                "yt-dlp: " + str(error)
            )

        try:
            return scrape_gallery_dl(
                url,
                platform
            )
        except Exception as error:
            errors.append(
                "gallery-dl: " + str(error)
            )

    elif platform in ["tiktok", "youtube"]:

        try:
            return scrape_ytdlp(
                url,
                platform
            )
        except Exception as error:
            errors.append(
                "yt-dlp: " + str(error)
            )

    elif platform in ["twitter", "facebook"]:

        try:
            return scrape_ytdlp(
                url,
                platform
            )
        except Exception as error:
            errors.append(
                "yt-dlp: " + str(error)
            )

        try:
            return scrape_gallery_dl(
                url,
                platform
            )
        except Exception as error:
            errors.append(
                "gallery-dl: " + str(error)
            )

    else:

        try:
            return scrape_ytdlp(
                url,
                platform
            )
        except Exception as error:
            errors.append(
                "yt-dlp: " + str(error)
            )

        try:
            return scrape_gallery_dl(
                url,
                platform
            )
        except Exception as error:
            errors.append(
                "gallery-dl: " + str(error)
            )

    raise Exception(
        "Nenhum extractor conseguiu processar a URL. "
        + " | ".join(errors)
    )


@app.post("/scrape")
def scrape(request: ScrapeRequest):
    platform = detect_platform(
        request.url
    )

    max_comments = min(
        max(request.max_comments, 0),
        100
    )

    try:
        return scrape_with_fallback(
            request.url,
            platform,
            max_comments
        )

    except Exception as error:
        raise HTTPException(
            status_code=422,
            detail={
                "platform": platform,
                "url": request.url,
                "error": str(error)
            }
        )


@app.get("/health")
def health():
    return {
        "status": "ok"
    }
