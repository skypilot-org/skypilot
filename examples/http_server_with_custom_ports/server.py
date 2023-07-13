import http.server
import socketserver

PORT = 33828


class MyHttpRequestHandler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        html = '''
            <html>
            <head>
                <title>Test Page</title>
            </head>
            <body>
                <h1>This is a demo HTML page.</h1>
            </body>
            </html>
        '''
        self.wfile.write(bytes(html, 'utf8'))
        return


Handler = MyHttpRequestHandler

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print("serving at port", PORT)
    httpd.serve_forever()
