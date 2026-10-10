import APIClient from '../../functions/APIClient';
import { DownloadRuleType } from '../actions/updateDownloadRule';

const loadDefaultDownloadRule = async () => {
  return APIClient<DownloadRuleType | null>('/api/download/rule/');
};

export default loadDefaultDownloadRule;
