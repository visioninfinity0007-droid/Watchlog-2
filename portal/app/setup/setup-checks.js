"use client";
import {Status} from "../owner/ui";
import s from "./customer.module.css";

// A short readiness ledger: each line is a label plus a status word (colour never stands alone).
export default function SetupChecks({items}){
  return <dl className={s.checks}>
    {items.map(x=><div key={x.label}>
      <dt>{x.label}</dt>
      <dd>{x.tone?<Status tone={x.tone}>{x.value}</Status>:<b>{x.value}</b>}</dd>
    </div>)}
  </dl>;
}
