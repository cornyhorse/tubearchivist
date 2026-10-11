import { useState } from 'react';
import { getRule, notBuilt, RuleBody, toRuleBody, Verdict } from '../../api/ux/client';
import Modal from '../ux/Modal';
import RuleEditor from './RuleEditor';

type VerdictBadgeProps = {
  verdict: Verdict;
  canEdit?: boolean;
  onChanged?: () => void;
};

const SCOPE_NAME: Record<string, string> = {
  default: 'the default rule',
  channel: 'the channel’s rule',
  playlist: 'the playlist’s rule',
  video: 'your choice for this video',
};

/** "allowed" or "skipped: longer than 1 h"; opens the rule that decided */
const VerdictBadge = ({ verdict, canEdit, onChanged }: VerdictBadgeProps) => {
  const [rule, setRule] = useState<RuleBody | null>();
  const ref = verdict.rule;
  const label = verdict.allowed
    ? verdict.override === 'download'
      ? 'download anyway'
      : 'allowed'
    : `skipped: ${verdict.reason ?? 'by a rule'}`;
  const detail = ref
    ? `Decided by ${SCOPE_NAME[ref.scope] ?? 'a rule'}: ${ref.summary}.`
    : 'No rule applies.';
  const className = `ux-verdict ${verdict.allowed ? 'allowed' : 'skipped'}`;

  if (!canEdit || !ref || ref.scope === 'video') {
    return (
      <span className={className} title={detail}>
        {label}
      </span>
    );
  }

  const open = async () => {
    const response = await getRule(
      ref.scope as 'default' | 'channel' | 'playlist',
      ref.target_id ?? undefined,
    );
    setRule(notBuilt(response) ? null : toRuleBody(response.data));
  };

  return (
    <>
      <button type="button" className={className} title={`${detail} Click to edit.`} onClick={open}>
        {label}
      </button>
      {rule !== undefined && (
        <Modal
          wide
          title={ref.scope === 'default' ? 'Default rule' : `Rule: ${ref.summary}`}
          onClose={() => setRule(undefined)}
        >
          <RuleEditor
            scope={ref.scope as 'default' | 'channel' | 'playlist'}
            targetId={ref.target_id || undefined}
            rule={rule}
            onSaved={() => {
              setRule(undefined);
              onChanged?.();
            }}
          />
        </Modal>
      )}
    </>
  );
};

export default VerdictBadge;
