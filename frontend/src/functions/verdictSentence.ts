import { PreviewItem } from '../api/ux/client';

/** "142 videos: 118 will download, 24 skipped (Shorts 20, longer than 1 h 4)" */
export const verdictSentence = (item: PreviewItem) => {
  const { total, download, skipped, already_have: have, reasons } = item.verdict;
  const noun = item.kind === 'video' ? 'video' : `${total.toLocaleString()} videos`;
  let text =
    item.kind === 'video'
      ? download
        ? 'This video will download'
        : have
          ? 'You already have this video'
          : 'This video is skipped'
      : `${noun}: ${download.toLocaleString()} will download, ${skipped.toLocaleString()} skipped`;
  if (item.kind !== 'video' && reasons.length) {
    text += ` (${reasons.map(reason => `${reason.label} ${reason.count}`).join(', ')})`;
  }
  if (item.kind !== 'video' && have) text += `, ${have.toLocaleString()} already downloaded`;
  if (item.kind === 'video' && !download && reasons.length) text += `: ${reasons[0].label}`;
  return `${text}.`;
};
