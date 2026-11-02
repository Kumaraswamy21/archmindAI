import Link from "next/link";
import { ArrowLeft } from "lucide-react";

export default function NotFound() { return <div className="not-found"><span className="eyebrow">404 / NOT FOUND</span><h1>This path has no map.</h1><p>The page may have moved or the address may be incomplete.</p><Link className="button button-primary" href="/"><ArrowLeft size={16} /> Return to overview</Link></div>; }
