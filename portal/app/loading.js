export default function Loading(){
  return <div className="routeLoadingShell" aria-label="Loading WatchLog" aria-busy="true">
    <aside className="routeLoadingRail" aria-hidden="true">
      <div className="routeLoadingBrand"/>
      <div className="routeLoadingButton"/>
      <div className="routeLoadingLine short"/>
      <div className="routeLoadingLine"/>
      <div className="routeLoadingLine short"/>
      <div className="routeLoadingLine"/>
      <div className="routeLoadingLine short"/>
    </aside>
    <main className="routeLoadingMain" aria-hidden="true">
      <div className="routeLoadingTop"><span/></div>
      <div className="routeLoadingCenter"><i/><b/><span/></div>
      <div className="routeLoadingComposer"/>
    </main>
  </div>;
}
