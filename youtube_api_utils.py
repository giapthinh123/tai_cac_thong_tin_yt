import re
from googleapiclient.discovery import build
import urllib.parse as urlparse
from urllib.parse import parse_qs

API_KEY = 'AIzaSyDt6wE07um8WDeJnEn3uRH9tgLpcyYEZbQ'

def get_youtube_client():
    return build('youtube', 'v3', developerKey=API_KEY)

def extract_channel_id(youtube, url: str) -> str:
    # Pattern for standard channel URL
    match = re.search(r"/channel/([\w-]+)", url)
    if match: return match.group(1)
    
    # Pattern for custom/user/@handle URL
    match = re.search(r"/c/([^/]+)", url)
    if match:
        res = youtube.channels().list(part='id', forUsername=match.group(1)).execute()
        if res.get('items'): return res['items'][0]['id']
        
    match = re.search(r"/user/([^/]+)", url)
    if match:
        res = youtube.channels().list(part='id', forUsername=match.group(1)).execute()
        if res.get('items'): return res['items'][0]['id']
        
    match = re.search(r"/@([^/]+)", url)
    if match:
        res = youtube.search().list(part='snippet', type='channel', q=match.group(1)).execute()
        if res.get('items'): return res['items'][0]['snippet']['channelId']
        
    raise ValueError("Invalid YouTube channel URL or could not resolve channel ID.")

def parse_url_type(url: str) -> tuple[str, str]:
    """Returns type ('video', 'playlist', 'channel') and the relevant ID/URL."""
    parsed = urlparse.urlparse(url)
    qs = parse_qs(parsed.query)
    
    if 'v' in qs:
        return 'video', qs['v'][0]
    
    match = re.search(r"youtu\.be/([\w-]+)", url)
    if match:
        return 'video', match.group(1)
        
    if 'list' in qs:
        return 'playlist', qs['list'][0]
        
    return 'channel', url

def fetch_videos_from_api(url: str, locales: list[str]) -> tuple[list[dict], str]:
    """
    Fetches video metadata using YouTube API.
    Returns:
    - A list of dicts: [{'id': str, 'default_title': str, 'thumbnail_url': str, 'localized_titles': dict}]
    - A string representing the collection title (channel name, playlist name, or video name) for subfolder.
    """
    youtube = get_youtube_client()
    url_type, extracted_val = parse_url_type(url)
    
    video_ids = []
    collection_title = "API_Download"
    
    if url_type == 'video':
        video_ids.append(extracted_val)
        res = youtube.videos().list(part='snippet', id=extracted_val).execute()
        if res.get('items'):
            collection_title = res['items'][0]['snippet']['title']
    elif url_type == 'playlist':
        res = youtube.playlists().list(part='snippet', id=extracted_val).execute()
        if res.get('items'):
            collection_title = res['items'][0]['snippet']['title']
        req = youtube.playlistItems().list(part='snippet', playlistId=extracted_val, maxResults=50)
        while req:
            res = req.execute()
            for item in res.get('items', []):
                # playlistItems returns videos that might be private/deleted
                if 'videoId' in item['snippet']['resourceId']:
                    video_ids.append(item['snippet']['resourceId']['videoId'])
            req = youtube.playlistItems().list_next(req, res)
    elif url_type == 'channel':
        channel_id = extract_channel_id(youtube, extracted_val)
        res = youtube.channels().list(part='snippet,contentDetails', id=channel_id).execute()
        if not res.get('items'):
            raise ValueError(f"Could not find channel details for ID {channel_id}")
        
        collection_title = res['items'][0]['snippet']['title']
        uploads_playlist = res['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        
        req = youtube.playlistItems().list(part='snippet', playlistId=uploads_playlist, maxResults=50)
        while req:
            res = req.execute()
            for item in res.get('items', []):
                if 'videoId' in item['snippet']['resourceId']:
                    video_ids.append(item['snippet']['resourceId']['videoId'])
            req = youtube.playlistItems().list_next(req, res)

    # Resolve metadata for video IDs in batches of 50
    final_videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        id_str = ','.join(batch)
        
        # Base request to get default snippet and thumbnails
        res = youtube.videos().list(part='snippet', id=id_str).execute()
        
        batch_videos = []
        # Store items in a dict mapped by id so we can merge localizations easily
        items_map = {}
        
        for item in res.get('items', []):
            vid_id = item['id']
            snippet = item['snippet']
            default_title = snippet['title']
            
            # Find best thumbnail
            thumbs = snippet.get('thumbnails', {})
            best_thumb = None
            for q in ['maxres', 'standard', 'high', 'medium', 'default']:
                if q in thumbs:
                    best_thumb = thumbs[q]['url']
                    break
                    
            vid_data = {
                'id': vid_id,
                'default_title': default_title,
                'thumbnail_url': best_thumb,
                'localized_titles': {}
            }
            batch_videos.append(vid_data)
            items_map[vid_id] = vid_data
            
        # For each locale, get localized titles using hl parameter
        for loc in locales:
            # Skip if no locales requested or list is empty
            if not loc:
                continue
            loc_res = youtube.videos().list(part='snippet', id=id_str, hl=loc).execute()
            for loc_item in loc_res.get('items', []):
                vid_id = loc_item['id']
                loc_title = loc_item['snippet']['title']
                if vid_id in items_map:
                    items_map[vid_id]['localized_titles'][loc] = loc_title
                    
        final_videos.extend(batch_videos)
        
    return final_videos, collection_title
