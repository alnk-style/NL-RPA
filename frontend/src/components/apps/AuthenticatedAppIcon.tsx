import { AppWindow } from 'lucide-react';
import {
  useEffect,
  useRef,
  useState,
} from 'react';

import api from '../../api/axios';
import { cn } from '../../lib/utils';

const iconObjectUrls = new Map<string, string>();
const iconRequests = new Map<string, Promise<string>>();

async function loadIconBlobUrl(iconUrl: string): Promise<string> {
  const cached = iconObjectUrls.get(iconUrl);
  if (cached) {
    return cached;
  }
  const pending = iconRequests.get(iconUrl);
  if (pending) {
    return pending;
  }
  const request = api
    .get<Blob>(iconUrl, { responseType: 'blob' })
    .then((response) => {
      const objectUrl = URL.createObjectURL(response.data);
      iconObjectUrls.set(iconUrl, objectUrl);
      return objectUrl;
    })
    .finally(() => {
      iconRequests.delete(iconUrl);
    });
  iconRequests.set(iconUrl, request);
  return request;
}

export function AuthenticatedAppIcon({
  iconUrl,
  className,
}: {
  iconUrl?: string;
  className?: string;
}) {
  const containerRef = useRef<HTMLSpanElement | null>(null);
  const [visible, setVisible] = useState(false);
  const [src, setSrc] = useState('');

  useEffect(() => {
    const element = containerRef.current;
    if (!iconUrl || !element) {
      return;
    }
    if (!('IntersectionObserver' in window)) {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: '160px' },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [iconUrl]);

  useEffect(() => {
    if (!iconUrl || !visible) {
      return;
    }
    let cancelled = false;
    void loadIconBlobUrl(iconUrl)
      .then((objectUrl) => {
        if (!cancelled) {
          setSrc(objectUrl);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSrc('');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [iconUrl, visible]);

  return (
    <span
      ref={containerRef}
      className={cn(
        'flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-md border border-slate-200 bg-slate-50 text-slate-400 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-500',
        className,
      )}
    >
      {src ? (
        <img
          src={src}
          alt=""
          className="h-full w-full object-contain p-1"
        />
      ) : (
        <AppWindow className="h-4 w-4" />
      )}
    </span>
  );
}
