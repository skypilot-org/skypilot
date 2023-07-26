import http.server
import socketserver

PORT = 8081


class MyHttpRequestHandler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        # Return 200 for all paths
        # Therefore, readiness_probe will return 200 at path '/health'
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        html = '''
            <html>
            <head>
                <title>SkyPilot Test Page</title>
            </head>
            <body>
                <h1>Hi, SkyPilot here!</h1>
            </body>
            </html>
        '''
        self.wfile.write(bytes(html, 'utf8'))
        return


Handler = MyHttpRequestHandler

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print("serving at port", PORT)
    httpd.serve_forever()
