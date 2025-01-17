import argparse
import http.server
import socketserver


class MyHttpRequestHandler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        # Return 200 for all paths
        # Therefore, readiness_probe will return 200 at path '/health'
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        html = """
            <html>
            <head>
                <title>SkyPilot Test Page</title>
            </head>
            <body>
                <h1>Hi, SkyPilot here!</h1>
            </body>
            </html>
        """
        self.wfile.write(bytes(html, 'utf8'))
        return


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SkyServe HTTP Test Server')
    parser.add_argument('--port', type=int, required=False, default=8080)
    args = parser.parse_args()

    Handler = MyHttpRequestHandler
    with socketserver.TCPServer(('', args.port), Handler) as httpd:
        print('serving at port', args.port)
        httpd.serve_forever()
