import APIClient from '../../functions/APIClient';

/** GET /api/queue/scheduled/ (docs/ux-api-contract.md §1); 501 until the jobs engine lands */
export type ScheduledJobType = {
  name: string;
  label: string;
  job_type: string;
  lane: string;
  schedule: string | null;
  schedule_human: string;
  enabled: boolean;
  next_run: string | null;
  last_run: string | null;
  last_job: { id: number; status: string; finished_at: string | null; error: string | null } | null;
};

const loadScheduledJobs = async () => {
  return APIClient<ScheduledJobType[]>('/api/queue/scheduled/');
};

export default loadScheduledJobs;
