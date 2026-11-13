import { useState } from 'react';
import { Globe2, LoaderCircle, Plus } from 'lucide-react';

import type { BrowserProfileRecord } from '../../types/connections';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '../ui/Card';
import { Input } from '../ui/Input';

export function ProfileList({
  profiles,
  creating,
  onCreate,
}: {
  profiles: BrowserProfileRecord[];
  creating: boolean;
  onCreate: (payload: {
    name: string;
    user_data_dir?: string | null;
    profile_directory?: string;
  }) => Promise<void>;
}) {
  const [name, setName] = useState('');
  const [userDataDir, setUserDataDir] = useState('');
  const [profileDirectory, setProfileDirectory] = useState('Default');

  async function submit() {
    if (!name.trim()) {
      return;
    }
    await onCreate({
      name: name.trim(),
      user_data_dir: userDataDir.trim() || null,
      profile_directory: profileDirectory.trim() || 'Default',
    });
    setName('');
    setUserDataDir('');
    setProfileDirectory('Default');
  }

  return (
    <Card>
      <CardHeader className="border-b border-slate-100">
        <CardTitle className="flex items-center gap-2">
          <Globe2 className="h-4 w-4 text-brand-600" />
          自动化浏览器 Profile
        </CardTitle>
        <p className="mt-1 text-xs text-slate-500">
          使用独立 Profile，避免占用日常浏览器的登录与缓存。
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {profiles.length ? (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {profiles.map((profile) => (
              <li
                key={profile.id}
                className="flex flex-wrap items-start justify-between gap-3 py-3"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{profile.name}</span>
                    <Badge
                      variant={
                        profile.login_state === 'ready'
                          ? 'success'
                          : profile.login_state === 'expired'
                            ? 'danger'
                            : 'secondary'
                      }
                    >
                      {profile.login_state}
                    </Badge>
                  </div>
                  <p className="mt-1 break-all text-xs text-slate-500">
                    {profile.user_data_dir || '使用默认自动化 Profile 目录'}
                    {` · ${profile.profile_directory}`}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="py-3 text-sm text-slate-500">尚未创建自动化 Profile。</p>
        )}

        <div className="grid gap-3 border-t border-slate-100 pt-4 sm:grid-cols-2 dark:border-slate-800">
          <Input
            label="名称"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：个人自动化"
          />
          <Input
            label="Profile 子目录"
            value={profileDirectory}
            onChange={(event) => setProfileDirectory(event.target.value)}
            placeholder="Default"
          />
          <Input
            label="用户数据目录"
            value={userDataDir}
            onChange={(event) => setUserDataDir(event.target.value)}
            placeholder="留空则使用默认隔离目录"
            className="sm:col-span-2"
          />
          <div className="sm:col-span-2">
            <Button
              size="sm"
              leftIcon={creating
                ? <LoaderCircle className="h-4 w-4 animate-spin" />
                : <Plus className="h-4 w-4" />}
              disabled={creating || !name.trim()}
              onClick={() => void submit()}
            >
              添加 Profile
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default ProfileList;
