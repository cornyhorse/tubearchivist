import { useState } from 'react';
import { DownloadRuleType } from '../../api/actions/updateDownloadRule';
import { EmbyNumberingType } from '../../api/actions/updateEmbyNumbering';
import { NUMBERING_SHORT, summarizeRule } from '../../functions/describeRule';
import DownloadRuleForm from '../DownloadRuleForm';
import EmbyNumberingSelect from '../EmbyNumberingSelect';
import Modal from './Modal';

type RuleBadgeProps = {
  channelId: string;
  channelName: string;
  rule: DownloadRuleType | null | undefined;
  defaultRule?: DownloadRuleType | null;
  /** without edit rights the badge is plain text */
  canEdit?: boolean;
  onSaved: () => void;
};

/** "No Shorts · > 10 min"; click to edit the channel's rule */
export const RuleBadge = ({
  channelId,
  channelName,
  rule,
  defaultRule,
  canEdit,
  onSaved,
}: RuleBadgeProps) => {
  const [open, setOpen] = useState(false);
  const summary = summarizeRule(rule, defaultRule);

  if (!canEdit) {
    return (
      <span className={`ux-badge rule ${summary.state}`} title={summary.detail}>
        {summary.label}
      </span>
    );
  }

  return (
    <>
      <button
        type="button"
        className={`ux-badge rule ${summary.state}`}
        title={`${summary.detail} Click to edit.`}
        aria-label={`Download rule for ${channelName}: ${summary.label}. Edit`}
        onClick={() => setOpen(true)}
      >
        {summary.label}
      </button>
      {open && (
        <Modal title={`Download rule: ${channelName}`} onClose={() => setOpen(false)}>
          <p className="ux-help">
            Which videos of this channel get downloaded when it's checked. Empty fields match
            everything. Without a rule here, the default rule from Settings applies. Videos you add
            by link are always downloaded.
          </p>
          <DownloadRuleForm
            key={JSON.stringify(rule ?? null)}
            channelId={channelId}
            rule={rule ?? null}
            onSaved={() => {
              setOpen(false);
              onSaved();
            }}
          />
        </Modal>
      )}
    </>
  );
};

const NUMBERING_HELP: Record<EmbyNumberingType, string> = {
  date: 'Season is the upload year; episode comes from the upload date.',
  year_frozen: 'Season is the upload year; episodes are numbered in order and never renumbered.',
  absolute_frozen: 'One season; episodes are numbered in order and never renumbered.',
  playlist_position: 'Episodes follow the playlist order.',
};

type NumberingBadgeProps = {
  kind: 'channel' | 'playlist';
  id: string;
  name: string;
  value: EmbyNumberingType | undefined;
  options: EmbyNumberingType[];
  canEdit?: boolean;
  onSaved: () => void;
};

/** "By date"; click to change how Emby numbers the episodes */
export const NumberingBadge = ({
  kind,
  id,
  name,
  value,
  options,
  canEdit,
  onSaved,
}: NumberingBadgeProps) => {
  const [open, setOpen] = useState(false);
  const scheme = value ?? options[0];
  const label = NUMBERING_SHORT[scheme];
  const detail = `Emby numbering: ${NUMBERING_HELP[scheme]}`;

  if (!canEdit) {
    return (
      <span className="ux-badge numbering" title={detail}>
        {label}
      </span>
    );
  }

  return (
    <>
      <button
        type="button"
        className="ux-badge numbering"
        title={`${detail} Click to change.`}
        aria-label={`Emby numbering for ${name}: ${label}. Change`}
        onClick={() => setOpen(true)}
      >
        {label}
      </button>
      {open && (
        <Modal title={`Emby numbering: ${name}`} onClose={() => setOpen(false)}>
          <p className="ux-help">
            How this {kind}'s videos are numbered as episodes in the Emby library. Numbers that are
            already assigned are kept when you add older videos.
          </p>
          <EmbyNumberingSelect
            kind={kind}
            id={id}
            value={scheme}
            options={options}
            onSaved={() => {
              setOpen(false);
              onSaved();
            }}
          />
        </Modal>
      )}
    </>
  );
};
