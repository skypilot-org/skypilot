import React from 'react';
import PropTypes from 'prop-types';
import '@/app/globals.css';
import { useEffect } from 'react';
import { BASE_PATH } from '@/data/connectors/constants';

function MyApp({ Component, pageProps }) {
  useEffect(() => {
    const link = document.createElement('link');
    link.rel = 'icon';
    link.href = `${BASE_PATH}/favicon.ico`;
    document.head.appendChild(link);
  }, []);

  return <Component {...pageProps} />;
}

MyApp.propTypes = {
  Component: PropTypes.elementType.isRequired,
  pageProps: PropTypes.object.isRequired,
};

export default MyApp;
