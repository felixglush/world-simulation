/** Executable source used by the unrelated project fixture. */
export interface Job {
  id: string;
  text: string;
}
export interface Result {
  id: string;
  characters: number;
}
export function processJob(job: Job): Result {
  return { id: job.id, characters: job.text.length };
}
