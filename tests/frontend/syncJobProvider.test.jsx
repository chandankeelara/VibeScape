// @vitest-environment jsdom
/**
 * frontend-next/src/features/sync/SyncJobProvider.jsx — a job note reaches the
 * toast instead of crashing the poll effect.
 *
 * The provider used to destructure `useToast()` as `{ toast }`, but the hook
 * returns the toast FUNCTION (with a `dismiss` property hung off it), so the
 * first status carrying a `note` threw "toast is not a function" inside the
 * effect that also drives autoplay and completion. This renders the real
 * ToastProvider around the real SyncJobProvider; only the network and the
 * player are faked.
 */
import { Component, useEffect } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';

const API = '../../frontend-next/src/lib/api';

const NOTE = 'Following the playlist owner so private tracks resolve.';
const fetchForVibe = vi.fn();

vi.mock('../../frontend-next/src/lib/api', () => ({
  ingestStatus: vi.fn(async () => ({
    status: 'running',
    note: NOTE,
    total: 10,
    processed: 0,
    added_to_library: 0,
    already_in_library: 0,
    queued_for_analysis: 0,
    skipped: 0,
    collecting: true,
  })),
  cancelIngest: vi.fn(async () => ({})),
}));

vi.mock('../../frontend-next/src/state/PlayerContext', () => ({
  usePlayer: () => ({ fetchForVibe }),
}));

/** Surfaces an effect error as text so the failure names the exception. */
class Boundary extends Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    return this.state.error
      ? <pre data-testid="crash">{String(this.state.error)}</pre>
      : this.props.children;
  }
}

async function mountWithRunningJob() {
  const { ToastProvider } = await import('../../frontend-next/src/state/ToastContext');
  const { SyncJobProvider, useSyncJob } = await import('../../frontend-next/src/features/sync/SyncJobProvider');

  function Starter() {
    const job = useSyncJob();
    useEffect(() => { job.begin('job-1'); }, []); // eslint-disable-line react-hooks/exhaustive-deps
    return <span data-testid="phase">{job.phase}</span>;
  }

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <Boundary>
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <SyncJobProvider>
            <Starter />
          </SyncJobProvider>
        </ToastProvider>
      </QueryClientProvider>
    </Boundary>,
  );
  return qc;
}

it('shows a job note as a toast instead of crashing the poll', async () => {
  await mountWithRunningJob();

  const toast = await screen.findByRole('status');
  expect(toast.textContent).toContain(NOTE);
  expect(screen.queryByTestId('crash')).toBeNull();
  // The effect that toasts is also the one that autoplays and completes;
  // the provider must still be alive after the note.
  expect(screen.getByTestId('phase').textContent).toBe('running');
});

it('toasts a note once per job even though the poll keeps delivering it', async () => {
  const api = await import(API);
  await mountWithRunningJob();
  await screen.findByRole('status');

  // Let a few more polls land with the same note.
  await new Promise((r) => setTimeout(r, 30));
  expect(api.ingestStatus).toHaveBeenCalled();
  expect(screen.getAllByRole('status')).toHaveLength(1);
});
