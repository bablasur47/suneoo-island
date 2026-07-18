import os
import re
import time
from urllib.parse import urlparse

import requests
from flask import Flask, send_from_directory, jsonify, request, Response

app = Flask(__name__, static_folder=None)

API_BASE = 'https://api.redgifs.com'
# Only allow downloads from known RedGifs media hosts (prevents SSRF/open proxy)
ALLOWED_DOWNLOAD_HOSTS = {
    'media.redgifs.com',
    'files.redgifs.com',
    'thumbs.redgifs.com',
    'thumbs2.redgifs.com',
    'thumbs3.redgifs.com',
    'thumbs4.redgifs.com',
}
ALLOWED_ORDERS = {'trending', 'top', 'top7', 'top28', 'latest', 'score', 'best', 'new'}
ALLOWED_TYPES = {'g', 'i'}  # g = videos/gifs, i = images
REQUEST_TIMEOUT = 15

token_data = {'token': None, 'expires_at': 0}
session = requests.Session()
session.headers.update({'User-Agent': 'Mozilla/5.0'})


def get_temp_token():
    if token_data['token'] and time.time() < token_data['expires_at']:
        return token_data['token']
    resp = session.get(f'{API_BASE}/v2/auth/temporary', timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    token_data['token'] = data['token']
    expires_in = data.get('expiresIn') or data.get('expires_in') or 3000
    token_data['expires_at'] = time.time() + max(60, int(expires_in) - 60)
    return data['token']


def api_get(path, params=None):
    token = get_temp_token()
    headers = {'Authorization': f'Bearer {token}'}
    resp = session.get(f'{API_BASE}{path}', params=params,
                       headers=headers, timeout=REQUEST_TIMEOUT)
    # Token may have been revoked early — refresh once and retry
    if resp.status_code == 401:
        token_data['token'] = None
        token = get_temp_token()
        headers = {'Authorization': f'Bearer {token}'}
        resp = session.get(f'{API_BASE}{path}', params=params,
                           headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


@app.after_request
def security_headers(resp):
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    return resp


@app.route('/')
def index():
    resp = send_from_directory('.', 'index.html')
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.route('/api/search')
def search():
    q = request.args.get('q', '').strip() or 'trending'
    order = request.args.get('order', 'trending')
    media_type = request.args.get('type', 'g')
    if order not in ALLOWED_ORDERS:
        order = 'trending'
    if media_type not in ALLOWED_TYPES:
        media_type = 'g'
    try:
        page = max(1, min(int(request.args.get('page', '1')), 1000))
    except ValueError:
        page = 1
    try:
        count = max(1, min(int(request.args.get('count', '40')), 80))
    except ValueError:
        count = 40
    try:
        params = {'type': media_type, 'tags': q, 'order': order,
                  'page': page, 'count': count}
        return jsonify(api_get('/v2/gifs/search', params))
    except Exception:
        app.logger.exception('Search failed')
        return jsonify({'error': 'Search failed — try again in a moment'}), 502


@app.route('/api/niches')
def niches():
    """Popular niches/categories from RedGifs. Falls back gracefully client-side."""
    try:
        count = max(1, min(int(request.args.get('count', '30')), 100))
    except ValueError:
        count = 30
    try:
        return jsonify(api_get('/v2/niches', {'count': count, 'order': 'subscribers'}))
    except Exception:
        app.logger.exception('Niches fetch failed')
        return jsonify({'error': 'Niches unavailable'}), 502


@app.route('/api/suggest')
def suggest():
    """Tag autocomplete."""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'tags': []})
    try:
        return jsonify(api_get('/v2/search/suggest', {'query': q}))
    except Exception:
        return jsonify({'tags': []})


@app.route('/api/download')
def download():
    url = request.args.get('url', '')
    if not url:
        return jsonify({'error': 'Missing url'}), 400
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        return jsonify({'error': 'URL not allowed'}), 403
    try:
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.redgifs.com/'}
        upstream = requests.get(url, headers=headers, stream=True, timeout=30)
        upstream.raise_for_status()
        filename = url.split('/')[-1].split('?')[0] or 'video.mp4'
        # Sanitize filename so it can't break out of the Content-Disposition header
        filename = re.sub(r'[^\w.-]', '_', filename) or 'video.mp4'
        response_headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
        if upstream.headers.get('content-length'):
            response_headers['Content-Length'] = upstream.headers['content-length']
        return Response(
            upstream.iter_content(chunk_size=64 * 1024),
            content_type=upstream.headers.get('content-type', 'video/mp4'),
            headers=response_headers,
        )
    except Exception:
        app.logger.exception('Download failed')
        return jsonify({'error': 'Download failed'}), 502


@app.errorhandler(404)
def not_found(_):
    return jsonify({'error': 'Not found'}), 404


def main():
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '127.0.0.1')
    debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true')
    print(f'Server running at http://localhost:{port}')
    if debug:
        app.run(host=host, port=port, debug=True)
        return
    # Production server: waitress if installed, otherwise threaded Flask
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port, threaded=True)


if __name__ == '__main__':
    main()
