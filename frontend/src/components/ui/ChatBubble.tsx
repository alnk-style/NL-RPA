import { Bot, UserRound } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import remarkGfm from 'remark-gfm';

import { cn } from '../../lib/utils';

export type ChatRole = 'user' | 'assistant' | 'ai';

export interface ChatBubbleProps {
  role: ChatRole;
  content: string;
  timestamp?: string;
  className?: string;
}

export function ChatBubble({
  role,
  content,
  timestamp,
  className,
}: ChatBubbleProps) {
  const isUser = role === 'user';

  return (
    <div
      className={cn(
        'flex w-full items-start gap-3',
        isUser && 'flex-row-reverse',
        className,
      )}
    >
      <div
        className={cn(
          'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border',
          isUser
            ? 'border-brand-200 bg-brand-50 text-brand-700 dark:border-brand-800 dark:bg-brand-900/50 dark:text-brand-100'
            : 'border-slate-200 bg-white text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300',
        )}
        aria-hidden="true"
      >
        {isUser ? (
          <UserRound className="h-4 w-4" />
        ) : (
          <Bot className="h-4 w-4" />
        )}
      </div>

      <div
        className={cn(
          'max-w-[min(82%,48rem)]',
          isUser ? 'items-end' : 'items-start',
        )}
      >
        <div
          className={cn(
            'rounded-lg px-4 py-3 text-sm shadow-sm',
            isUser
              ? 'bg-brand-600 text-white'
              : 'border border-slate-200 bg-white text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200',
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap break-words leading-7">
              {content}
            </p>
          ) : (
            <div className="markdown-content break-words">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
                components={{
                  p: ({ children }) => (
                    <p className="mb-3 leading-7 last:mb-0">{children}</p>
                  ),
                  ul: ({ children }) => (
                    <ul className="mb-3 list-disc space-y-1 pl-5 last:mb-0">
                      {children}
                    </ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="mb-3 list-decimal space-y-1 pl-5 last:mb-0">
                      {children}
                    </ol>
                  ),
                  li: ({ children }) => (
                    <li className="leading-7">{children}</li>
                  ),
                  h1: ({ children }) => (
                    <h1 className="mb-3 mt-5 text-xl font-semibold first:mt-0">
                      {children}
                    </h1>
                  ),
                  h2: ({ children }) => (
                    <h2 className="mb-3 mt-5 text-lg font-semibold first:mt-0">
                      {children}
                    </h2>
                  ),
                  h3: ({ children }) => (
                    <h3 className="mb-2 mt-4 text-base font-semibold first:mt-0">
                      {children}
                    </h3>
                  ),
                  a: ({ children, ...props }) => (
                    <a
                      className="font-medium text-brand-600 underline underline-offset-2 hover:text-brand-700 dark:text-brand-300"
                      target="_blank"
                      rel="noreferrer"
                      {...props}
                    >
                      {children}
                    </a>
                  ),
                  blockquote: ({ children }) => (
                    <blockquote className="mb-3 border-l-2 border-slate-300 pl-3 italic text-slate-500 dark:border-slate-600 dark:text-slate-400">
                      {children}
                    </blockquote>
                  ),
                  pre: ({ children }) => (
                    <pre className="mb-3 max-h-[28rem] overflow-auto rounded-md bg-slate-950 p-3 text-xs leading-6 text-slate-100 last:mb-0">
                      {children}
                    </pre>
                  ),
                  code: ({ className, children, ...props }) => (
                    <code
                      className={cn(
                        className
                          ? 'hljs font-mono'
                          : 'rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[0.85em] text-slate-800 dark:bg-slate-800 dark:text-slate-100',
                        className,
                      )}
                      {...props}
                    >
                      {children}
                    </code>
                  ),
                }}
              >
                {content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {timestamp ? (
          <p
            className={cn(
              'mt-1.5 text-xs text-slate-400',
              isUser ? 'text-right' : 'text-left',
            )}
          >
            {timestamp}
          </p>
        ) : null}
      </div>
    </div>
  );
}

export default ChatBubble;
