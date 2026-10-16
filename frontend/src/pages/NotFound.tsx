import { Link } from "react-router-dom";

import { PageHeader } from "../components/ui";

export function NotFound() {
  return (
    <>
      <PageHeader title="Page not found" subtitle="That page doesn't exist." />
      <Link to="/" className="text-[14px] font-medium text-accent hover:underline">
        Back to the overview
      </Link>
    </>
  );
}
