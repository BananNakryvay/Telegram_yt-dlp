import re
import telebot
import yt_dlp
import os
import math
from yt_dlp import download_range_func
from requests import get
import urllib.parse as parseurl
import random
import string
from threading import Timer
from flask import Flask, send_from_directory, abort
from flask import request

#get public IP
ip = get('https://api.ipify.org').content.decode('utf8')

bot = telebot.TeleBot(BOT_TOKEN)

# Path to save the downloaded videos
DOWNLOAD_PATH = './downloads/'

# Port for the file server
SERVER_PORT = 5000

# Ensure the download path exists
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# Create Flask app for file serving
app = Flask(__name__, static_folder=DOWNLOAD_PATH)

def get_video_info(url):
    """Fetch video information using yt_dlp."""
    with yt_dlp.YoutubeDL({}) as ydl:
        return ydl.extract_info(url, download=False)

def convert_size(size_bytes):
    """Convert bytes to a human-readable format."""
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def filter_filesize_per_resolution(formats):
    """Filter and return video formats with approximate file sizes."""
    resolutions = {}
    for fmt in formats:
        if fmt.get('vcodec') != 'none' and fmt.get('ext') == 'mp4':  # Only consider video formats mp4
            resolution = fmt.get('format_note')
            filesize = fmt.get('filesize_approx') or 0
            if resolution and filesize > 0 and resolution not in resolutions:
                resolutions[resolution] = {
                    'id': fmt.get('format_id'),
                    'filesize': convert_size(filesize),
                    'format_note': fmt.get('format_note')
                }
    return resolutions

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Send me a YouTube link and I'll help you download the video!")

@bot.message_handler(commands=['list'])
def list_formats(message):
    url = message.text.split(maxsplit=1)[1]  # Extract the URL after the /list command
    bot.reply_to(message, "Fetching available formats...")

    try:
        info_dict = get_video_info(url)
        formats = info_dict.get('formats', [])
        list_of_formats = [
            f"```ID:{fmt.get('format_id')}``` {fmt.get('resolution')} - {convert_size(fmt.get('filesize_approx') or 0)}"
            for fmt in formats
        ]
        reply_text = "\n".join(list_of_formats)
        bot.reply_to(message, f"Available formats:\n{parse_text(reply_text)}", parse_mode='MarkdownV2')
    except Exception as e:
        bot.reply_to(message, f"An error occurred: {e}")

@bot.message_handler(func=lambda message: 'ID:' in message.text)
def handle_format_message(message):
    url = message.text.split()[0]
    timestamp = extract_time_param(message.text, 't')
    timestop = extract_time_param(message.text, 'n')
    bot.reply_to(message, "Downloading the video from the specified format...")
    download_video(message, url, None, timestamp, timestop)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    url = message.text
    timestamp = extract_time_param(message.text, 't')
    timestop = extract_time_param(message.text, 'n')
    bot.reply_to(message, "Analyzing the video...")

    try:
        info_dict = get_video_info(url)
        formats = info_dict.get('formats', [])
        video_options = filter_filesize_per_resolution(formats)
        best_audio_id = "ba[ext=m4a]"
        list_of_formats = [
            f"{resolution} - {details['filesize']} ID:{details['id']}"
            for  resolution, details in video_options.items()
        ]
        list_of_formats.append("MP3") # Add MP3 option to the list

        if timestamp or timestop:
            markup = telebot.types.ReplyKeyboardMarkup(one_time_keyboard=True, row_width=2, resize_keyboard=True)
            markup.add("With timestamps", "Without timestamps")
            msg = bot.reply_to(message, "Do you want to download with timestamps?", reply_markup=markup)
            bot.register_next_step_handler(msg, stampcheck, url, best_audio_id, timestamp, timestop, list_of_formats, video=message)
        else:
            keyboad_markup(message, url, best_audio_id, list_of_formats)
    except Exception as e:
        bot.reply_to(message, f"An error occurred: {e}")

def keyboad_markup(message, url,best_audio_id, list_of_formats, timestamp=None, timestop=None):
    markup = telebot.types.ReplyKeyboardMarkup(one_time_keyboard=True, row_width=3, resize_keyboard=True)
    markup.add(*list_of_formats)
    msg = bot.reply_to(message, "Choose a video format:", reply_markup=markup)
    bot.register_next_step_handler(msg, download_video, url, best_audio_id, timestamp, timestop)

def stampcheck(msg, url, best_audio_id, timestamp, timestop, list_of_formats, video):
    if msg.text == "With timestamps":
         keyboad_markup(video, url,best_audio_id, list_of_formats, timestamp, timestop)
    else:
        keyboad_markup(video, url,best_audio_id, list_of_formats)

def download_video(message, url, best_audio_id=None, start=None, end=None):
    resolution = message.text
    is_audio_only = resolution == "MP3"
    format_id = best_audio_id if is_audio_only else resolution.split("ID:")[-1]
    format_str = format_id if is_audio_only or not best_audio_id else f'{format_id}+{best_audio_id}'
    extension = '.mp3' if is_audio_only else '.%(ext)s'
    output_path = os.path.join(DOWNLOAD_PATH,f'%(format_id)s%(id)s{str(start or "")}{str(end or "")}', f'%(title)s{extension}')

    ydl_opts = {
        'format': format_str,
        'outtmpl': output_path,
        'socket_timeout': 30,
    }

    if start or end:
        ydl_opts.update({
            'verbose': True,
            'download_ranges': download_range_func(None, [(float(start or 0), float(end or -1))]),
            'force_keyframes_at_cuts': True
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            output_filename = ydl.prepare_filename(info_dict).replace('\\', '/')
            video_folder = output_filename.split('/')[1]
            video_file_path = f"{ip}:{SERVER_PORT}/files/{video_folder}"
            title = info_dict['title']

            file_size = os.path.getsize(output_filename)
            if file_size < 50 * 1024 * 1024:  # 50 MB
                with open(output_filename, 'rb') as media_file:
                    if is_audio_only:
                        bot.send_audio(message.chat.id, audio=media_file, title=title)
                    else:
                        bot.send_video(message.chat.id, video=media_file, caption=title,  supports_streaming=True)

            bot.send_message(message.chat.id, text = f"<a href='{video_file_path}'>{title}</a>", parse_mode ="HTML")

            Timer(90, lambda: delete_file(output_filename)).start()
    except Exception as e:
        media_type = "MP3" if is_audio_only else "video"
        bot.reply_to(message, f"Failed to download {media_type}: {e}")

def parse_text(text):
    """Escape special characters in text for MarkdownV2."""
    return re.sub(r'[_*[\]()~>#\+\-=|{}.!]', lambda x: '\\' + x.group(), text)

def extract_time_param(text, param):
    """Extract time parameter from the message text."""
    match = re.search(fr'{param}=(.*?)(?:\s|n=|$)', text)
    return time_to_seconds(match.group(1)) if match else None

def time_to_seconds(time_str):
    """Convert a time string to seconds."""
    if not time_str:
        return None
    time_pattern = re.compile(r'(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?')
    match = time_pattern.match(time_str)
    if not match:
        raise ValueError(f"Invalid time format: {time_str}")
    hours = int(match.group('hours') or 0)
    minutes = int(match.group('minutes') or 0)
    seconds = int(match.group('seconds') or 0)
    return hours * 3600 + minutes * 60 + seconds

def delete_file(file_path):
    """Delete a file after a certain time."""
    try:
        os.remove(file_path)
        print(f"File deleted: {file_path}")
    except FileNotFoundError:
        print(f"File not found: {file_path}")

# Flask route to serve files
@app.route('/files/<path:folder>')
def serve_file(folder):
    directory = os.path.join(DOWNLOAD_PATH, folder)
    try:
        # List all files in the directory
        files = os.listdir(directory)

        # If the directory is empty or no files found, raise 404
        if not files:
            abort(404)

        # Assuming you want to serve the first file found
        filename = files[0]

        # Send the file from the specified directory
        return send_from_directory(directory, filename)
    except FileNotFoundError:
        abort(404)

# Run the Flask server
def run_server():
    app.run(host='0.0.0.0', port=SERVER_PORT, debug=False)

# Start the Flask server in a separate thread
from threading import Thread
server_thread = Thread(target=run_server)
server_thread.start()

# Start polling for Telegram messages
bot.polling()