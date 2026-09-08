import os
import re
import json
import base64
import tempfile
import subprocess

from urllib.parse import urlparse

import instaloader
import yt_dlp

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


# ==========================================================
# API
# ==========================================================

app = FastAPI(
    title="Social Scraper API",
    version="2.1.0"
)


MAX_COMMENTS = 30


# ==========================================================
# REQUEST
# ==========================================================

class ScrapeRequest(BaseModel):
    url: str
    max_comments: int = MAX_COMMENTS


# ==========================================================
# COOKIES
# ==========================================================

def get_cookie_file():
    """
    Le SOCIAL_COOKIES_BASE64 do ambiente do Render,
    decodifica e cria temporariamente um cookies.txt.

    Retorna o caminho do arquivo ou None.
    """

    cookies_base64 = os.getenv(
        "SOCIAL_COOKIES_BASE64"
    )

    if not cookies_base64:
        return None

    try:
        cookie_bytes = base64.b64decode(
            cookies_base64
        )

        cookie_path = os.path.join(
            tempfile.gettempdir(),
            "social-cookies.txt"
        )

        with open(
            cookie_path,
            "wb"
        ) as file:
            file.write(
                cookie_bytes
            )

        return cookie_path

    except Exception as error:
        print(
            "Erro ao carregar cookies:",
            str(error)
        )

        return None


# ==========================================================
# IDENTIFICACAO DA PLATAFORMA
# ==========================================================

def detect_platform(url: str) -> str:

    hostname = (
        urlparse(url).hostname
        or ""
    ).lower()

    if hostname.startswith("www."):
        hostname = hostname[4:]

    if hostname in [
        "instagram.com",
        "instagr.am"
    ]:
        return "instagram"

    if hostname in [
        "tiktok.com",
        "vm.tiktok.com",
        "vt.tiktok.com"
    ]:
        return "tiktok"

    if hostname in [
        "youtube.com",
        "youtu.be",
        "m.youtube.com",
        "music.youtube.com"
    ]:
        return "youtube"

    if hostname in [
        "twitter.com",
        "x.com",
        "mobile.twitter.com",
        "mobile.x.com"
    ]:
        return "twitter"

    if hostname in [
        "facebook.com",
        "m.facebook.com",
        "web.facebook.com",
        "fb.watch"
    ]:
        return "facebook"

    return "generic"


# ==========================================================
# INSTAGRAM
# ==========================================================

def extract_instagram_shortcode(url: str):

    patterns = [
        r"/p/([^/?#]+)",
        r"/reel/([^/?#]+)",
        r"/reels/([^/?#]+)",
        r"/tv/([^/?#]+)"
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            url
        )

        if match:
            return match.group(1)

    return None


def scrape_instagram(
    url: str,
    max_comments: int
):

    shortcode = extract_instagram_shortcode(
        url
    )

    if not shortcode:
        raise Exception(
            "Nao foi possivel identificar "
            "o shortcode do Instagram."
        )

    loader = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
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

    except Exception as error:
        print(
            "Erro ao coletar midia Instagram:",
            str(error)
        )

    return {
        "success": True,
        "platform": "instagram",
        "source": "instaloader",
        "url": url,
        "author": post.owner_username,
        "description": post.caption or None,
        "date": (
            post.date_utc.isoformat()
            if post.date_utc
            else None
        ),
        "likes": post.likes,
        "comments_count": post.comments,
        "comments": [],
        "comments_accessible": False,
        "media": media
    }


# ==========================================================
# YT-DLP
# ==========================================================

def scrape_ytdlp(
    url: str,
    platform: str
):

    cookie_file = get_cookie_file()

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False
    }

    if cookie_file:
        options["cookiefile"] = cookie_file

    with yt_dlp.YoutubeDL(
        options
    ) as ydl:

        info = ydl.extract_info(
            url,
            download=False
        )

    if not info:
        raise Exception(
            "yt-dlp nao retornou dados."
        )

    description = (
        info.get("description")
        or info.get("fulltitle")
        or info.get("title")
    )

    media = []

    thumbnail = info.get(
        "thumbnail"
    )

    if thumbnail:
        media.append(
            thumbnail
        )

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
        "description": description or None,
        "title": info.get("title"),
        "date": info.get("upload_date"),
        "likes": info.get("like_count"),
        "comments_count": info.get(
            "comment_count"
        ),
        "comments": [],
        "comments_accessible": False,
        "media": media
    }


# ==========================================================
# GALLERY-DL
# ==========================================================

def scrape_gallery_dl(
    url: str,
    platform: str
):

    cookie_file = get_cookie_file()

    command = [
        "gallery-dl",
        "--verbose",
        "--dump-json"
    ]

    if cookie_file:
        command.extend([
            "--cookies",
            cookie_file
        ])

    command.append(
        url
    )

    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60
    )

    if process.returncode != 0:

        raise Exception(
            process.stderr.strip()
            or "gallery-dl falhou."
        )

    lines = [
        line
        for line
        in process.stdout.splitlines()
        if line.strip()
    ]

    items = []

    for line in lines:

        try:
            items.append(
                json.loads(line)
            )

        except Exception:
            continue

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

        if isinstance(
            value,
            dict
        ):

            if not description:

                possible_description = (
                    value.get("description")
                    or value.get("content")
                    or value.get("caption")
                    or value.get("tweet")
                    or value.get("text")
                    or value.get("title")
                    or value.get("content_text")
                )

                if isinstance(
                    possible_description,
                    str
                ):
                    description = (
                        possible_description
                    )

            if not author:

                possible_author = (
                    value.get("username")
                    or value.get("screen_name")
                    or value.get("user")
                    or value.get("author")
                    or value.get("owner")
                    or value.get("name")
                )

                if isinstance(
                    possible_author,
                    str
                ):
                    author = (
                        possible_author
                    )

            possible_url = value.get(
                "url"
            )

            if (
                isinstance(
                    possible_url,
                    str
                )
                and possible_url.startswith(
                    "http"
                )
            ):
                media.append(
                    possible_url
                )

            for child in value.values():
                walk(child)

        elif isinstance(
            value,
            list
        ):

            for child in value:
                walk(child)

    walk(items)

    media = list(
        dict.fromkeys(
            media
        )
    )

    return {
        "success": True,
        "platform": platform,
        "source": "gallery-dl",
        "url": url,
        "author": author,
        "description": description,
        "comments": [],
        "comments_accessible": False,
        "media": media[:10]
    }


# ==========================================================
# FALLBACK
# ==========================================================

def scrape_with_fallback(
    url: str,
    platform: str,
    max_comments: int
):

    errors = []

    # ======================================================
    # INSTAGRAM
    # ======================================================

    if platform == "instagram":

        try:
            return scrape_instagram(
                url,
                max_comments
            )

        except Exception as error:
            errors.append(
                "Instaloader: "
                + str(error)
            )

        try:
            return scrape_ytdlp(
                url,
                platform
            )

        except Exception as error:
            errors.append(
                "yt-dlp: "
                + str(error)
            )

        try:
            return scrape_gallery_dl(
                url,
                platform
            )

        except Exception as error:
            errors.append(
                "gallery-dl: "
                + str(error)
            )

    # ======================================================
    # X / TWITTER
    # ======================================================

    elif platform == "twitter":

        # Primeiro gallery-dl
        try:
            return scrape_gallery_dl(
                url,
                platform
            )

        except Exception as error:
            errors.append(
                "gallery-dl: "
                + str(error)
            )

        # Depois yt-dlp
        try:
            return scrape_ytdlp(
                url,
                platform
            )

        except Exception as error:
            errors.append(
                "yt-dlp: "
                + str(error)
            )

    # ======================================================
    # FACEBOOK
    # ======================================================

    elif platform == "facebook":

        try:
            return scrape_ytdlp(
                url,
                platform
            )

        except Exception as error:
            errors.append(
                "yt-dlp: "
                + str(error)
            )

        try:
            return scrape_gallery_dl(
                url,
                platform
            )

        except Exception as error:
            errors.append(
                "gallery-dl: "
                + str(error)
            )

    # ======================================================
    # TIKTOK
    # ======================================================

    elif platform == "tiktok":

        try:
            return scrape_ytdlp(
                url,
                platform
            )

        except Exception as error:
            errors.append(
                "yt-dlp: "
                + str(error)
            )

        try:
            return scrape_gallery_dl(
                url,
                platform
            )

        except Exception as error:
            errors.append(
                "gallery-dl: "
                + str(error)
            )

    # ======================================================
    # YOUTUBE
    # ======================================================

    elif platform == "youtube":

        try:
            return scrape_ytdlp(
                url,
                platform
            )

        except Exception as error:
            errors.append(
                "yt-dlp: "
                + str(error)
            )

    # ======================================================
    # GENERICO
    # ======================================================

    else:

        try:
            return scrape_ytdlp(
                url,
                platform
            )

        except Exception as error:
            errors.append(
                "yt-dlp: "
                + str(error)
            )

        try:
            return scrape_gallery_dl(
                url,
                platform
            )

        except Exception as error:
            errors.append(
                "gallery-dl: "
                + str(error)
            )

    # ======================================================
    # NENHUM FUNCIONOU
    # ======================================================

    return {
        "success": False,
        "platform": platform,
        "source": "none",
        "url": url,
        "author": None,
        "description": None,
        "comments": [],
        "comments_accessible": False,
        "media": [],
        "errors": errors
    }


# ==========================================================
# ENDPOINT SCRAPE
# ==========================================================

@app.post("/scrape")
def scrape(
    request: ScrapeRequest
):

    platform = detect_platform(
        request.url
    )

    max_comments = min(
        max(
            request.max_comments,
            0
        ),
        100
    )

    try:

        result = scrape_with_fallback(
            request.url,
            platform,
            max_comments
        )

        return result

    except Exception as error:

        raise HTTPException(
            status_code=500,
            detail={
                "platform": platform,
                "url": request.url,
                "error": str(error)
            }
        )


# ==========================================================
# HEALTH CHECK
# ==========================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "cookies_loaded": bool(
            os.getenv(
                "SOCIAL_COOKIES_BASE64"
            )
        ),
        "version": "2.1.0"
    }