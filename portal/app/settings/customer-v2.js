"use client";
import AddSite from "./add-site";
import SiteList from "./site-list";
import useCustomerSettings from "./use-customer-settings";
import {OwnerPage,Section,Row,Notice,Loading} from "../owner/ui";

export default function CustomerSettingsV2(){
  const s=useCustomerSettings();
  const loading=!s.email&&!s.error;
  return <OwnerPage active="Settings" email={s.email} siteId={s.siteId}
    kicker={["Account",s.current?.name]}
    title="Account and sites"
    actions={<a className="ow-btn quiet" href="/settings/account/">Billing & account</a>}>
    {s.error&&<Notice tone="bad">{s.error}</Notice>}
    {loading?<Loading label="Loading account and sites"/>:<>
      <Section first title="Sites" count={s.sites.length||null} note="Choose a site to manage its settings. Guided Setup connects a new site's cameras.">
        <AddSite/>
        <SiteList settings={s}/>
      </Section>

      <Section title="Account and team" note="These settings apply to every site on this account.">
        <div className="ow-rows">
          <Row compact tone="neutral" title="Team" body="People with access, their roles and pending invitations." action={<a href="/team/">Manage team</a>}/>
          <Row compact tone="neutral" title="Plan, billing and agreements" body="Current plan, account status, payments and documents." action={<a href="/settings/account/">Open</a>}/>
          <Row compact tone="neutral" title="Your sign-in" body={s.email||"Signed in"} meta={[s.role?"Role: "+s.role.replace(/^./,c=>c.toUpperCase()):null]}/>
        </div>
      </Section>
    </>}
  </OwnerPage>;
}
