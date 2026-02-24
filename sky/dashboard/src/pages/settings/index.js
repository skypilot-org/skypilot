export async function getServerSideProps() {
  return {
    redirect: {
      destination: '/settings/config',
      permanent: false,
    },
  };
}

export default function SettingsIndexPage() {
  return null;
}
