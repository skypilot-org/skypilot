import React from 'react';
import Link from 'next/link';
import {
  ExternalLinkIcon,
  GitHubIcon,
  SlackIcon,
  CommentFeedbackIcon,
} from '@/components/elements/icons';
import { CustomTooltip } from '@/components/utils';

export function Footer() {
  return (
    <footer className="bg-gray-100 border-t border-gray-200 py-6 px-4 md:px-6">
      <div className="mx-auto flex flex-col sm:flex-row items-center justify-between w-full max-w-screen-xl">
        <div className="text-sm text-gray-600 mb-4 sm:mb-0">
          © {new Date().getFullYear()} SkyPilot. All rights reserved.
        </div>
        <div className="flex items-center space-x-2">
          <CustomTooltip
            content="Documentation"
            className="text-sm text-muted-foreground"
          >
            <a
              href="https://skypilot.readthedocs.io/en/latest/"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center px-2 py-1 text-gray-600 hover:text-blue-600 transition-colors duration-150 cursor-pointer"
              title="Docs"
            >
              <span className="mr-1">Docs</span>
              <ExternalLinkIcon className="w-4 h-4" />
            </a>
          </CustomTooltip>

          <CustomTooltip
            content="GitHub Repository"
            className="text-sm text-muted-foreground"
          >
            <a
              href="https://github.com/skypilot-org/skypilot"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center p-2 rounded-full text-gray-600 hover:bg-gray-100 transition-colors duration-150 cursor-pointer"
              title="GitHub"
            >
              <GitHubIcon className="w-5 h-5" />
            </a>
          </CustomTooltip>
          <CustomTooltip
            content="Slack Community"
            className="text-sm text-muted-foreground"
          >
            <a
              href="https://slack.skypilot.org"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center p-2 rounded-full text-gray-600 hover:bg-gray-100 transition-colors duration-150 cursor-pointer"
              title="Slack"
            >
              <SlackIcon className="w-5 h-5" />
            </a>
          </CustomTooltip>
          <CustomTooltip
            content="Feedback"
            className="text-sm text-muted-foreground"
          >
            <a
              href="https://github.com/skypilot-org/skypilot/issues/new/choose"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center p-2 rounded-full text-gray-600 hover:bg-gray-100 transition-colors duration-150 cursor-pointer"
              title="Feedback"
            >
              <CommentFeedbackIcon className="w-5 h-5" />
            </a>
          </CustomTooltip>
        </div>
      </div>
    </footer>
  );
}
