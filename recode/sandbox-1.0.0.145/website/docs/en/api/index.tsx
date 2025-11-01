export const frontmatter = {
  pageType: 'custom',
};

import { useDark, useI18n } from '@rspress/core/runtime';
import { Suspense, lazy } from 'react';
import './index.scss';

const ApiReferenceReact = lazy(() =>
  import('@scalar/api-reference-react').then((mod) => ({
    default: mod.ApiReferenceReact,
  })),
);

import '@scalar/api-reference-react/style.css';

export const APIPage = () => {
  const dark = useDark();
  const t = useI18n();

  return (
    <Suspense
      fallback={
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            alignItems: 'center',
            height: '60vh',
            gap: '20px',
          }}
        >
          <div
            style={{
              width: '40px',
              height: '40px',
              border: '3px solid #f3f3f3',
              borderTop: '3px solid #3498db',
              borderRadius: '50%',
              animation: 'spin 1s linear infinite',
            }}
          />
          <div style={{ color: '#666', fontSize: '14px' }}>
            {t('loadingApiReference')}
          </div>
        </div>
      }
    >
      <ApiReferenceReact
        key={dark ? 'dark' : 'light'}
        configuration={{
          url: '/v1/openapi.json',
          darkMode: dark,
          forceDarkModeState: dark ? 'dark' : 'light',
          hideTestRequestButton: true,
          hideDownloadButton: true,
          hideDarkModeToggle: true,
          hideClientButton: true,
          hideModels: true,
          telemetry: false,
          documentDownloadType: 'json',
        }}
      />
    </Suspense>
  );
};

export default APIPage;
