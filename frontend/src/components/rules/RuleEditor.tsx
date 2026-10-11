import { useEffect, useId, useState } from 'react';
import {
  deleteRule,
  listRulePresets,
  notBuilt,
  previewRule,
  RuleBody,
  RulePreset,
  RulePreview,
  RuleScope,
  saveRule,
} from '../../api/ux/client';
import updateDownloadRule from '../../api/actions/updateDownloadRule';
import { describeRuleParts } from '../../functions/describeRule';
import { EMPTY_RULE, FALLBACK_PRESETS } from './ruleDefaults';
import { duration } from '../../functions/timeText';

const TYPES: { value: RuleBody['vid_types'][number]; label: string }[] = [
  { value: 'videos', label: 'Videos' },
  { value: 'streams', label: 'Livestreams' },
  { value: 'shorts', label: 'Shorts' },
];

const toMinutes = (seconds: number | null | undefined) =>
  seconds === null || seconds === undefined ? '' : String(seconds / 60);
const fromMinutes = (minutes: string) =>
  minutes.trim() === '' ? null : Math.round(Number(minutes) * 60);

/** is this preset's patch already part of the rule? */
const presetActive = (preset: RulePreset, rule: RuleBody) => {
  if (preset.needs_value && preset.value_field) {
    return Boolean(rule[preset.value_field as keyof RuleBody]);
  }
  return Object.entries(preset.patch).every(
    ([key, value]) => JSON.stringify(rule[key as keyof RuleBody]) === JSON.stringify(value),
  );
};

type RuleEditorProps = {
  scope: RuleScope;
  targetId?: string;
  /** null: no rule yet */
  rule: RuleBody | null;
  /** "draft" only reports changes (Add dialog); "save" stores the rule */
  mode?: 'save' | 'draft';
  onChange?: (rule: RuleBody) => void;
  onSaved?: () => void;
  /** wait this long after a change before previewing */
  previewDelay?: number;
};

/** presets, the rule's fields, and a live preview of what it would do */
const RuleEditor = ({
  scope,
  targetId,
  rule,
  mode = 'save',
  onChange,
  onSaved,
  previewDelay = 400,
}: RuleEditorProps) => {
  const id = useId();
  const [draft, setDraft] = useState<RuleBody>({ ...EMPTY_RULE, ...(rule ?? {}) });
  const [presets, setPresets] = useState<RulePreset[]>(FALLBACK_PRESETS);
  const [askValue, setAskValue] = useState<RulePreset | null>(null);
  const [value, setValue] = useState('');
  const [preview, setPreview] = useState<RulePreview | null>();
  const [status, setStatus] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const response = await listRulePresets();
        if (!notBuilt(response) && Array.isArray(response.data)) setPresets(response.data);
      } catch {
        // keep the built-in presets
      }
    })();
  }, []);

  useEffect(() => {
    const timer = setTimeout(async () => {
      try {
        const response = await previewRule({ scope, target_id: targetId, rule: draft, limit: 50 });
        setPreview(notBuilt(response) ? null : (response.data ?? null));
      } catch {
        setPreview(null);
      }
    }, previewDelay);
    return () => clearTimeout(timer);
  }, [draft, scope, targetId, previewDelay]);

  const update = (changes: Partial<RuleBody>) => {
    const next = { ...draft, ...changes };
    setDraft(next);
    onChange?.(next);
  };

  const applyPreset = (preset: RulePreset) => {
    if (preset.needs_value) {
      setAskValue(preset);
      setValue('');
      return;
    }
    update(preset.patch as Partial<RuleBody>);
  };

  const save = async () => {
    setStatus('Saving…');
    try {
      let response: { status: number; error?: unknown } = await saveRule(scope, targetId, draft);
      if (notBuilt(response) && scope !== 'playlist') {
        response = await updateDownloadRule(scope === 'default' ? undefined : targetId, draft);
      }
      if (response.status >= 400) throw response.error;
      setStatus('Saved');
      onSaved?.();
    } catch (error) {
      setStatus(`Not saved: ${(error as { error?: string })?.error ?? 'something went wrong'}`);
    }
  };

  const remove = async () => {
    setStatus('Removing…');
    let response: { status: number } = await deleteRule(scope, targetId);
    if (notBuilt(response) && scope !== 'playlist') {
      response = await updateDownloadRule(scope === 'default' ? undefined : targetId, null);
    }
    setStatus(response.status >= 400 ? 'Not removed' : 'Removed');
    if (response.status < 400) onSaved?.();
  };

  const parts = describeRuleParts(draft);

  return (
    <div className="ux-rule-editor">
      <div className="ux-chips" role="group" aria-label="Quick rules">
        {presets.map(preset => (
          <button
            key={preset.key}
            type="button"
            className="ux-chip"
            aria-pressed={presetActive(preset, draft)}
            title={preset.description}
            onClick={() => applyPreset(preset)}
          >
            {preset.label}
          </button>
        ))}
      </div>
      {askValue && (
        <form
          className="ux-inline-form ux-preset-value"
          onSubmit={event => {
            event.preventDefault();
            if (!value.trim() || !askValue.value_field) return;
            update({ ...(askValue.patch as Partial<RuleBody>), [askValue.value_field]: value });
            setAskValue(null);
          }}
        >
          <label htmlFor={`${id}-value`}>{askValue.label.replace('…', '')}</label>
          <input
            id={`${id}-value`}
            type={askValue.needs_value === 'date' ? 'date' : 'text'}
            value={value}
            onChange={event => setValue(event.target.value)}
          />
          <button type="submit">Apply</button>
          <button type="button" className="ux-link-button" onClick={() => setAskValue(null)}>
            Cancel
          </button>
        </form>
      )}

      <p className="ux-setting-effect">
        {draft.enabled
          ? parts.length
            ? `Downloads only: ${parts.join(' · ')}.`
            : 'Matches every video.'
          : 'Switched off: every video is downloaded.'}
      </p>

      <div className="ux-rule-fields">
        <label className="ux-switch">
          <input
            type="checkbox"
            role="switch"
            checked={draft.enabled}
            onChange={event => update({ enabled: event.target.checked })}
          />
          <span>Rule on</span>
        </label>
        <fieldset>
          <legend>Types</legend>
          {TYPES.map(type => (
            <label key={type.value}>
              <input
                type="checkbox"
                checked={draft.vid_types.length === 0 || draft.vid_types.includes(type.value)}
                onChange={event => {
                  const current = draft.vid_types.length
                    ? draft.vid_types
                    : TYPES.map(t => t.value);
                  const next = event.target.checked
                    ? [...current, type.value]
                    : current.filter(item => item !== type.value);
                  update({ vid_types: next.length === TYPES.length ? [] : next });
                }}
              />{' '}
              {type.label}
            </label>
          ))}
        </fieldset>
        <label htmlFor={`${id}-min`}>Longer than (minutes)</label>
        <input
          id={`${id}-min`}
          type="number"
          min={0}
          step="0.5"
          value={toMinutes(draft.min_duration)}
          onChange={event => update({ min_duration: fromMinutes(event.target.value) })}
        />
        <label htmlFor={`${id}-max`}>Shorter than (minutes)</label>
        <input
          id={`${id}-max`}
          type="number"
          min={0}
          step="0.5"
          value={toMinutes(draft.max_duration)}
          onChange={event => update({ max_duration: fromMinutes(event.target.value) })}
        />
        <label htmlFor={`${id}-include`}>Title contains (regex)</label>
        <input
          id={`${id}-include`}
          type="text"
          placeholder="e.g. coaster|train"
          value={draft.title_include}
          onChange={event => update({ title_include: event.target.value })}
        />
        <label htmlFor={`${id}-exclude`}>Title doesn’t contain (regex)</label>
        <input
          id={`${id}-exclude`}
          type="text"
          placeholder="e.g. #shorts|trailer"
          value={draft.title_exclude}
          onChange={event => update({ title_exclude: event.target.value })}
        />
        <label htmlFor={`${id}-after`}>Uploaded on or after</label>
        <input
          id={`${id}-after`}
          type="date"
          value={draft.published_after ?? ''}
          onChange={event => update({ published_after: event.target.value || null })}
        />
        <label htmlFor={`${id}-action`}>Videos that don’t match</label>
        <select
          id={`${id}-action`}
          value={draft.action}
          onChange={event => update({ action: event.target.value as RuleBody['action'] })}
        >
          <option value="ignore">Keep them as ignored, with the reason</option>
          <option value="skip">Leave them out entirely</option>
        </select>
      </div>

      <section className="ux-rule-preview" aria-live="polite" aria-label="Preview">
        {preview === undefined && <p className="ux-muted">Working out what this rule does…</p>}
        {preview === null && (
          <p className="ux-muted">The preview needs a newer server; the rule still saves.</p>
        )}
        {preview && (
          <>
            <p>
              <strong>
                Of the last {preview.total} videos: {preview.download} download, {preview.skipped}{' '}
                skipped
              </strong>
              {preview.reasons.length > 0 &&
                ` (${preview.reasons.map(reason => `${reason.label} ${reason.count}`).join(', ')})`}
            </p>
            <ul className="ux-preview-list">
              {preview.videos.slice(0, 8).map(video => (
                <li key={video.youtube_id}>
                  <span className={`ux-verdict ${video.verdict.allowed ? 'allowed' : 'skipped'}`}>
                    {video.verdict.allowed ? 'download' : `skip: ${video.verdict.reason}`}
                  </span>{' '}
                  {video.title} <span className="ux-muted">{duration(video.duration_seconds)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      {mode === 'save' && (
        <div className="button-box">
          <button type="button" onClick={save}>
            Save rule
          </button>
          {rule && (
            <button type="button" className="ux-link-button" onClick={remove}>
              {scope === 'default' ? 'Remove the default rule' : 'Remove, use the default rule'}
            </button>
          )}
          <span role="status" className="ux-setting-status">
            {status}
          </span>
        </div>
      )}
    </div>
  );
};

export default RuleEditor;
