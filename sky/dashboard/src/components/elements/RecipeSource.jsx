import React from 'react';
import { GitBranchIcon } from 'lucide-react';

// Helpers for recipes whose content is managed outside the API server and
// described by `recipe.source` (e.g. {kind: 'git', url, ref, sha, path}).

export function isGitSourced(recipe) {
  return recipe?.source?.kind === 'git';
}

export function shortSha(sha) {
  return sha ? sha.slice(0, 7) : '';
}

// URL of the recipe's file in the repository, at `rev` (a sha or a ref).
export function getSourceFileUrl(source, rev) {
  if (!source?.url || !source?.path) return source?.url || null;
  const base = source.url.replace(/\/+$/, '');
  return `${base}/blob/${rev || source.ref}/${source.path}`;
}

export function getSourceCommitUrl(source) {
  if (!source?.url || !source?.sha) return null;
  return `${source.url.replace(/\/+$/, '')}/commit/${source.sha}`;
}

export function getSourceRepoName(source) {
  if (!source?.url) return '';
  return source.url.replace(/\/+$/, '').split('/').slice(-2).join('/');
}

const GIT_BADGE_CLASS =
  'inline-flex items-center gap-1 flex-shrink-0 px-1.5 py-0.5 text-xs font-medium leading-none text-sky-700 bg-sky-50 border border-sky-200 rounded-full';

// "Git" pill marking a git-sourced recipe. Pass `href` to render it as a link.
export function GitBadge({ href }) {
  const className = GIT_BADGE_CLASS;
  const content = (
    <>
      <GitBranchIcon className="w-3 h-3" />
      Git
    </>
  );
  if (href) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className={`${className} hover:bg-sky-100`}
      >
        {content}
      </a>
    );
  }
  return <span className={className}>{content}</span>;
}

// Author of a git-sourced recipe.
export function GitAuthor() {
  return <GitBadge />;
}
