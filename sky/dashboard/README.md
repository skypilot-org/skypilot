This is a [Next.js](https://nextjs.org/) project bootstrapped with [`create-next-app`](https://github.com/vercel/next.js/tree/canary/packages/create-next-app).

## Getting Started

Install nextjs:

```bash
# You may need to clear your cache if you have permission issues
# npm cache clean --force

# Install all dependencies in the current directory
npm install
```

By default, the dashboard will connect to your local API server at `http://127.0.0.1:46580`. If you want to connect to another API server, set the environment variable:

```bash
export SKYPILOT_API_SERVER_ENDPOINT=http://username:password@skypilot-api.domain.com:30050
```

Run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying the files. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/basic-features/font-optimization) to automatically optimize and load Inter, a custom Google Font.

Execute `npm run lint` for static code analysis and `npm run format` to format the code with `prettier`.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js/) - your feedback and contributions are welcome!
