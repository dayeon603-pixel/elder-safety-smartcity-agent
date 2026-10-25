import type { ReactNode } from "react";
import type { AsyncState } from "../api/useApi";
import { ErrorState } from "./Notice";
import { SkeletonText } from "./Skeleton";

/** Render loading skeleton / error with retry / ready content from a useApi state. */
export function AsyncBoundary<T>({
  state, onRetry, children, skeleton, errorTitle,
}: {
  state: AsyncState<T>;
  onRetry: () => void;
  children: (data: T) => ReactNode;
  skeleton?: ReactNode;
  errorTitle?: string;
}) {
  if (state.status === "loading") return <>{skeleton ?? <SkeletonText lines={4} />}</>;
  if (state.status === "error") return <ErrorState title={errorTitle} message={state.message} onRetry={onRetry} />;
  return <>{children(state.data)}</>;
}
