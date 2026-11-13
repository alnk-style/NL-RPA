import { Mail } from 'lucide-react';

import { deliveryStatusVariant } from '../../lib/execution';
import type { ExecutionDeliveryRecord } from '../../types/workflow';
import { Badge } from '../ui/Badge';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '../ui/Card';

export function DeliveryPanel({
  deliveries,
}: {
  deliveries: ExecutionDeliveryRecord[];
}) {
  if (!deliveries.length) {
    return null;
  }
  return (
    <Card>
      <CardHeader className="border-b border-slate-100">
        <CardTitle>交付记录</CardTitle>
        <p className="mt-1 text-xs text-slate-500">
          邮件是否投递以 SMTP message-id 和投递状态为准。
        </p>
      </CardHeader>
      <CardContent className="p-0">
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {deliveries.map((delivery) => (
            <li key={delivery.id} className="space-y-2 px-4 py-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Mail className="h-4 w-4 text-slate-400" />
                <span className="font-medium">{delivery.channel}</span>
                <Badge variant={deliveryStatusVariant(delivery.status)}>
                  {delivery.status}
                </Badge>
                {delivery.step_id ? (
                  <span className="text-xs text-slate-500">
                    步骤 {delivery.step_id}
                  </span>
                ) : null}
              </div>
              <p className="break-all text-xs text-slate-500">
                收件人 {JSON.stringify(delivery.recipient ?? [])}
              </p>
              {delivery.provider_message_id ? (
                <p className="break-all font-mono text-[11px] text-slate-500">
                  message-id: {delivery.provider_message_id}
                </p>
              ) : null}
              {delivery.error ? (
                <p className="break-all text-xs text-red-600">
                  {delivery.error}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

export default DeliveryPanel;
