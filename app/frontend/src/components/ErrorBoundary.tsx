import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { Button } from "./Button";

interface Props {
  children: ReactNode;
  /** Changing this value (e.g. the route path) clears a previous error. */
  resetKey?: string;
  onError?: (error: Error, info: ErrorInfo) => void;
}
interface State {
  error: Error | null;
  resetKey: string | undefined;
}

/** Catches render errors below it and offers a retry that re-mounts the subtree. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey !== state.resetKey ? { error: null, resetKey: props.resetKey } : null;
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.props.onError?.(error, info);
  }

  private retry = (): void => this.setState({ error: null });

  render(): ReactNode {
    const { error } = this.state;
    if (error === null) return this.props.children;
    return (
      <div role="alert" className="mx-auto my-12 max-w-prose border border-line bg-surface p-6">
        <h2 className="text-h3">화면을 표시하는 중 문제가 생겼습니다</h2>
        <p className="mt-2 text-small text-ink-muted">
          입력하신 내용은 서버에 저장되지 않았을 수 있습니다. 다시 시도해도 같은 문제가 반복되면 페이지를 새로고침해 주세요.
        </p>
        <details className="mt-4 text-small text-ink-muted">
          <summary className="cursor-pointer">기술 정보</summary>
          <pre className="mt-2 overflow-x-auto bg-surface-sunken p-3 font-mono text-caption">{error.message}</pre>
        </details>
        <div className="mt-6 flex gap-2">
          <Button variant="primary" onClick={this.retry}>
            다시 시도
          </Button>
          <Button onClick={() => window.location.reload()}>새로고침</Button>
        </div>
      </div>
    );
  }
}
