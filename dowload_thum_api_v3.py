import os
import re
import requests
from googleapiclient.discovery import build
from PIL import Image
import numpy as np
import io

def extract_channel_id(api_key, channel_url):
    # Pattern for standard channel URL
    channel_pattern = re.compile(r"(https?://)?(www\.)?youtube\.com/channel/([a-zA-Z0-9_-]+)")
    match = channel_pattern.match(channel_url)
    if match:
        return match.group(3)
    
    # Pattern for custom URL
    custom_pattern = re.compile(r"(https?://)?(www\.)?youtube\.com/c/([a-zA-Z0-9_-]+)")
    match = custom_pattern.match(channel_url)
    if match:
        custom_name = match.group(3)
        return get_channel_id_from_custom_url(api_key, custom_name)
    
    # Pattern for user URL
    user_pattern = re.compile(r"(https?://)?(www\.)?youtube\.com/user/([a-zA-Z0-9_-]+)")
    match = user_pattern.match(channel_url)
    if match:
        user_name = match.group(3)
        return get_channel_id_from_user_url(api_key, user_name)
    
    # Pattern for handle URL
    handle_pattern = re.compile(r"(https?://)?(www\.)?youtube\.com/@([a-zA-Z0-9_-]+)")
    match = handle_pattern.match(channel_url)
    if match:
        handle_name = match.group(3)
        return get_channel_id_from_handle(api_key, handle_name)
    
    raise ValueError("Invalid YouTube channel URL")

def get_channel_id_from_custom_url(api_key, custom_name):
    url = f"https://www.googleapis.com/youtube/v3/channels?part=id&forUsername={custom_name}&key={api_key}"
    response = requests.get(url)
    response_data = response.json()
    if "items" in response_data and len(response_data["items"]) > 0:
        return response_data["items"][0]["id"]
    raise ValueError("Invalid custom channel URL or API key")

def get_channel_id_from_user_url(api_key, user_name):
    url = f"https://www.googleapis.com/youtube/v3/channels?part=id&forUsername={user_name}&key={api_key}"
    response = requests.get(url)
    response_data = response.json()
    if "items" in response_data and len(response_data["items"]) > 0:
        return response_data["items"][0]["id"]
    raise ValueError("Invalid user channel URL or API key")

def get_channel_id_from_handle(api_key, handle_name):
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={handle_name}&key={api_key}"
    response = requests.get(url)
    response_data = response.json()
    if "items" in response_data and len(response_data["items"]) > 0:
        return response_data["items"][0]["snippet"]["channelId"]
    raise ValueError("Invalid handle or API key")

def get_video_ids(api_key, channel_id, max_results=50):
    youtube = build('youtube', 'v3', developerKey=api_key)
    video_ids = []
    request = youtube.channels().list(
        part='contentDetails',
        id=channel_id
    )
    response = request.execute()

    uploads_playlist_id = response['items'][0]['contentDetails']['relatedPlaylists']['uploads']
    
    request = youtube.playlistItems().list(
        part='snippet',
        playlistId=uploads_playlist_id,
        maxResults=max_results
    )
    
    while request:
        response = request.execute()
        for item in response['items']:
            video_id = item['snippet']['resourceId']['videoId']
            video_title = item['snippet']['title']
            
            thumbnails = item['snippet'].get('thumbnails', {})
            thumbnail_url = None
            for quality in ['maxres', 'standard', 'high', 'medium', 'default']:
                if quality in thumbnails:
                    thumbnail_url = thumbnails[quality]['url']
                    break
                    
            video_ids.append({
                'id': video_id,
                'title': video_title,
                'publishedAt': item['snippet']['publishedAt'],
                'thumbnail_url': thumbnail_url
            })
        request = youtube.playlistItems().list_next(request, response)
    
    # Sort videos by published date (newest first)
    video_ids.sort(key=lambda x: x['publishedAt'], reverse=True)
    
    return video_ids

def download_thumbnails(video_ids, save_folder, num_thumbnails, hue_shift, saturation_shift, value_shift):
    if not os.path.exists(save_folder):
        os.makedirs(save_folder)

    for index, video in enumerate(video_ids[:num_thumbnails], start=1):
        thumbnail_url = video.get('thumbnail_url')
        video_title = video['title']
        sanitized_title = sanitize_file_name(video_title)
        download_thumbnail(thumbnail_url, sanitized_title, index, save_folder, hue_shift, saturation_shift, value_shift)

def download_thumbnail(thumbnail_url, video_title, sequence_number, save_folder, hue_shift, saturation_shift, value_shift):
    if thumbnail_url:
        response = requests.get(thumbnail_url)
        if response.status_code == 200:
            if not os.path.exists(save_folder):
                os.makedirs(save_folder)
            image = Image.open(io.BytesIO(response.content))
            edited_image = edit_image(image, hue_shift, saturation_shift, value_shift)
            edited_image.save(os.path.join(save_folder, f"{sequence_number:03d}_{video_title}.jpg"))
            print(f"Downloaded and edited thumbnail for video {sequence_number}: {video_title}")
            return True
    return False

def sanitize_file_name(file_name):
    # Remove illegal characters for Windows file names
    illegal_chars = r'<>:"/\|?*'
    for char in illegal_chars:
        file_name = file_name.replace(char, '')
    return file_name[:255]  # Limit to 255 characters for file name length

def edit_image(image, hue_shift, saturation_shift, value_shift):
    image = image.convert('RGB')
    np_img = np.array(image)
    hsv_img = rgb_to_hsv(np_img)
    hsv_img[:, :, 0] = (hsv_img[:, :, 0] + hue_shift / 360.0) % 1.0
    hsv_img[:, :, 1] = np.clip(hsv_img[:, :, 1] * (1 + saturation_shift / 100.0), 0, 1)
    hsv_img[:, :, 2] = np.clip(hsv_img[:, :, 2] * (1 + value_shift / 100.0), 0, 1)
    rgb_img = hsv_to_rgb(hsv_img)
    return Image.fromarray((rgb_img * 255).astype(np.uint8))

def rgb_to_hsv(rgb):
    rgb = rgb / 255.0
    hsv = np.zeros_like(rgb)
    maxc = rgb.max(axis=-1)
    minc = rgb.min(axis=-1)
    hsv[..., 2] = maxc
    diff = maxc - minc
    mask = maxc != minc
    hsv[mask, 1] = (diff[mask] / maxc[mask])
    rc = (maxc - rgb[..., 0]) / (diff + 1e-10)
    gc = (maxc - rgb[..., 1]) / (diff + 1e-10)
    bc = (maxc - rgb[..., 2]) / (diff + 1e-10)
    hsv[..., 0] = np.select([rgb[..., 0] == maxc, rgb[..., 1] == maxc], [bc - gc, 2.0 + rc - bc], default=4.0 + gc - rc)
    hsv[..., 0] = (hsv[..., 0] / 6.0) % 1.0
    return hsv

def hsv_to_rgb(hsv):
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    i = (h * 6.0).astype(int)
    f = (h * 6.0) - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i = i % 6
    rgb = np.zeros_like(hsv)
    rgb[..., 0] = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [v, q, p, p, t, v])
    rgb[..., 1] = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [t, v, v, q, p, p])
    rgb[..., 2] = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5], [p, p, t, v, v, q])
    return rgb

def main():
#    api_key = input("Enter your YouTube Data API key: ")
    api_key = 'AIzaSyDt6wE07um8WDeJnEn3uRH9tgLpcyYEZbQ'
    channel_url = input("Enter the YouTube channel URL: ")
    num_thumbnails = int(input("Enter the number of thumbnails to download: "))
    save_folder = 'thumbnails'
    
    hue_shift = float(input("Enter hue shift (degrees): "))
    saturation_shift = float(input("Enter saturation shift (%): "))
    value_shift = float(input("Enter value (brightness) shift (%): "))

    try:
        channel_id = extract_channel_id(api_key, channel_url)
        video_ids = get_video_ids(api_key, channel_id, num_thumbnails)
        download_thumbnails(video_ids, save_folder, num_thumbnails, hue_shift, saturation_shift, value_shift)
    except ValueError as e:
        print(e)

if __name__ == "__main__":
    main()
