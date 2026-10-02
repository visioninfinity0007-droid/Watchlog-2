export default function Loading(){
  return <div className="wl-loading" role="status" aria-label="Loading WatchLog" aria-busy="true">
    <aside className="wl-loading-nav" aria-hidden="true"><i/><i/><i/><i/><i/><i/></aside>
    <main className="wl-loading-main" aria-hidden="true">
      <div className="wl-loading-head"><i/><b/></div>
      <div className="wl-loading-rows"><div><span/></div><div><span/></div><div><span/></div><div><span/></div></div>
    </main>
  </div>;
}
