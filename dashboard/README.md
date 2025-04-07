This is a [Next.js](https://nextjs.org/) project bootstrapped with [`create-next-app`](https://github.com/vercel/next.js/tree/canary/packages/create-next-app).

## Getting Started

Install nextjs:

```bash
# You may need to clear your cache if you have permission issues
# npm cache clean --force

# Install all dependencies in the current directory
npm install
```

First, run the development server:

```bash
export SKYPILOT_API_SERVER_ENDPOINT=http://username:password@skypilot-api.domain.com:30050

npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.js`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/basic-features/font-optimization) to automatically optimize and load Inter, a custom Google Font.

Execute `npm run lint` for static code analysis and `npm run format` to format the code with `prettier`.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js/) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/deployment) for more details.

## Deploy with SkyPilot

```
sky launch -c web deploy.yaml --env WEB_PASSWORD=yourpassword
```

Find the endpoint

```
sky status --endpoint 8000 web
34.27.154.158:8000
```

Log in with the password you set in the environment variable `WEB_PASSWORD`.

To update the deployment, run the following command:

```
sky exec web --workdir . "npm run build && npm restart"
```
