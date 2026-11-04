import argparse
import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import cv2
import numpy as np
from rapidocr import RapidOCR
from pcb_app.inventory import read_labels

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    engine = RapidOCR()
    token = config['token']

    class Handler(BaseHTTPRequestHandler):

        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != '/labels' or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.send_error(403)
                return
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 12 * 1024 * 1024:
                self.send_error(413)
                return
            try:
                data = self.rfile.read(length)
                image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if image is None or image.shape[0] * image.shape[1] > 20000000:
                    raise ValueError('Invalid image')
                result = {'labels': read_labels(image, engine)}
                output = json.dumps(result).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(output)))
                self.end_headers()
                self.wfile.write(output)
            except Exception:
                self.send_error(500, 'OCR failed')
    server = HTTPServer((config['host'], config['port']), Handler)
    print('Reference OCR ready', flush=True)
    server.serve_forever()
if __name__ == '__main__':
    main()
