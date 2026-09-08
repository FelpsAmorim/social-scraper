import os
import re
import json
import base64
import tempfile
import subprocess
import requests

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
    version="2.3.0"
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
# X / TWITTER - FXTWITTER
# ==========================================================

def extract_twitter_parts(url: str):

    match = re.search(
        r"(?:x\.com|twitter\.com)/([^/]+)/status/(\d+)",
        url
    )

    if not match:
        return None, None

    username = match.group(1)
    tweet_id = match.group(2)

    return username, tweet_id


def scrape_twitter_fxtwitter(url: str):

    username, tweet_id = extract_twitter_parts(
        url
    )

    if not username or not tweet_id:

        raise Exception(
            "Nao foi possivel identificar "
            "usuario e tweet_id do X."
        )

    api_url = (
        "https://api.fxtwitter.com/"
        + username
        + "/status/"
        + tweet_id
    )

    response = requests.get(
        api_url,
        timeout=20,
        headers={
            "User-Agent":
                "GeoPulse-Social-Scraper/1.0"
        }
    )

    if response.status_code != 200:

        raise Exception(
            "FxTwitter HTTP "
            + str(response.status_code)
            + ": "
            + response.text[:500]
        )

    data = response.json()

    tweet = (
        data.get("tweet")
        or data.get("status")
    )

    if not tweet:

        raise Exception(
            "FxTwitter nao retornou "
            "o objeto do post."
        )

    description = (
        tweet.get("text")
        or tweet.get("content")
        or tweet.get("description")
    )

    author_data = (
        tweet.get("author")
        or {}
    )

    author = (
        author_data.get("name")
        or author_data.get("screen_name")
        or author_data.get("username")
        or author_data.get("nick")
    )

    media = []

    media_data = tweet.get("media")

    if isinstance(media_data, dict):

        photos = (
            media_data.get("photos")
            or []
        )

        videos = (
            media_data.get("videos")
            or []
        )

        for item in photos:

            if isinstance(item, dict):

                media_url = (
                    item.get("url")
                    or item.get("media_url")
                )

                if media_url:
                    media.append(
                        media_url
                    )

        for item in videos:

            if isinstance(item, dict):

                media_url = (
                    item.get("url")
                    or item.get("thumbnail_url")
                )

                if media_url:
                    media.append(
                        media_url
                    )

    elif isinstance(media_data, list):

        for item in media_data:

            if isinstance(item, dict):

                media_url = (
                    item.get("url")
                    or item.get("media_url")
                    or item.get("thumbnail_url")
                )

                if media_url:
                    media.append(
                        media_url
                    )

    likes = (
        tweet.get("likes")
        or tweet.get("favorite_count")
    )

    replies = (
        tweet.get("replies")
        or tweet.get("reply_count")
    )

    date = (
        tweet.get("created_at")
        or tweet.get("date")
    )

    if not description:

        raise Exception(
            "FxTwitter acessou o post, "
            "mas nao retornou texto."
        )

    return {
        "success": True,
        "platform": "twitter",
        "source": "fxtwitter",
        "url": url,
        "author": author,
        "description": description,
        "date": date,
        "likes": likes,
        "comments_count": replies,
        "comments": [],
        "comments_accessible": False,
        "media": media[:10]
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

        options["cookiefile"] = (
            cookie_file
        )

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

    thumbnail = (
        info.get("thumbnail")
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
        "description": (
            description
            or None
        ),
        "title": info.get(
            "title"
        ),
        "date": info.get(
            "upload_date"
        ),
        "likes": info.get(
            "like_count"
        ),
        "comments_count": info.get(
            "comment_count"
        ),
        "comments": [],
        "comments_accessible": False,
        "media": media
    }


# ==========================================================
# GALLERY-DL - TWITTER
# ==========================================================

def scrape_twitter_gallery_dl(
    url: str
):

    cookie_file = get_cookie_file()

    command = [
        "gallery-dl",
        "--simulate",
        "--print",
        "CONTENT::{content}",
        "--print",
        "AUTHOR::{author[name]}",
        "--print",
        "USERNAME::{author[nick]}",
        "--print",
        "TWEET_ID::{tweet_id}",
        "--print",
        "DATE::{date}"
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

    output = process.stdout.strip()

    if not output:

        raise Exception(
            "gallery-dl nao retornou dados."
        )

    description = None
    author = None
    username = None
    tweet_id = None
    date = None

    for line in output.splitlines():

        line = line.strip()

        if line.startswith(
            "CONTENT::"
        ):

            value = line.replace(
                "CONTENT::",
                "",
                1
            ).strip()

            if value and value.lower() not in [
                "none",
                "null"
            ]:

                description = value

        elif line.startswith(
            "AUTHOR::"
        ):

            value = line.replace(
                "AUTHOR::",
                "",
                1
            ).strip()

            if value and value.lower() not in [
                "none",
                "null"
            ]:

                author = value

        elif line.startswith(
            "USERNAME::"
        ):

            value = line.replace(
                "USERNAME::",
                "",
                1
            ).strip()

            if value and value.lower() not in [
                "none",
                "null"
            ]:

                username = value

        elif line.startswith(
            "TWEET_ID::"
        ):

            value = line.replace(
                "TWEET_ID::",
                "",
                1
            ).strip()

            if value and value.lower() not in [
                "none",
                "null"
            ]:

                tweet_id = value

        elif line.startswith(
            "DATE::"
        ):

            value = line.replace(
                "DATE::",
                "",
                1
            ).strip()

            if value and value.lower() not in [
                "none",
                "null"
            ]:

                date = value

    if not author and username:

        author = username

    if not description:

        raise Exception(
            "gallery-dl acessou o post, "
            "mas nao retornou content."
        )

    return {
        "success": True,
        "platform": "twitter",
        "source": "gallery-dl",
        "url": url,
        "author": author,
        "description": description,
        "tweet_id": tweet_id,
        "date": date,
        "comments": [],
        "comments_accessible": False,
        "media": []
    }


# ==========================================================
# GALLERY-DL GENERICO
# ==========================================================

def scrape_gallery_dl(
    url: str,
    platform: str
):

    if platform == "twitter":

        return scrape_twitter_gallery_dl(
            url
        )

    cookie_file = get_cookie_file()

    command = [
        "gallery-dl",
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
        for line in process.stdout.splitlines()
        if line.strip()
    ]

    items = []

    for line in lines:

        try:

            items.append(
                json.loads(
                    line
                )
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

            possible_url = (
                value.get("url")
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

            for child in (
                value.values()
            ):

                walk(
                    child
                )

        elif isinstance(
            value,
            list
        ):

            for child in value:

                walk(
                    child
                )

    walk(
        items
    )

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

        # 1 - FxTwitter
        try:

            return scrape_twitter_fxtwitter(
                url
            )

        except Exception as error:

            errors.append(
                "FxTwitter: "
                + str(error)
            )

        # 2 - gallery-dl
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

        # 3 - yt-dlp
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
# SCRAPE ENDPOINT
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
# HEALTH
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
        "version": "2.3.0"
    }