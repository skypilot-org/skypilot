import { Html, Head, Main, NextScript } from 'next/document';
import { NO_FLASH_SCRIPT } from '@/lib/theme';

export default function Document() {
  return (
    <Html lang="en">
      <Head>
        {/* Sets the theme class before first paint. The server adds the CSP
            nonce to inline scripts, so this needs no CSP change. */}
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_SCRIPT }} />
      </Head>
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}
