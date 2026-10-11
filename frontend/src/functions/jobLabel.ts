import { Job } from '../api/ux/client';

/** a job in one line: "Download “Title”", "Check channel Trains" */
export const jobLabel = (job: Job) => {
  if (job.label) return job.label;
  const title = job.target?.title;
  if (!title) return job.type_label;
  return job.target.kind === 'video'
    ? `${job.type_label} “${title}”`
    : `${job.type_label}: ${title}`;
};
