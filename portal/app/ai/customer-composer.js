export default function CustomerComposer({chat,styles}){
  return <footer className={styles.composerWrap}>
    <small className={styles.disclaimer}>WatchLog can make mistakes. Check important information.</small>
    <div className={styles.composer}>
      <textarea
        className={styles.composerInput}
        ref={chat.inputRef}
        rows={1}
        value={chat.draft}
        onChange={e=>chat.setDraft(e.target.value)}
        onKeyDown={e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();chat.send()}}}
        placeholder={`Ask WatchLog about ${chat.site?.name||"this site"}...`}
        aria-label="Ask WatchLog"
      />
      <button type="button" aria-label="Send" disabled={!chat.draft.trim()||chat.busy} onClick={()=>chat.send()}>↑</button>
    </div>
  </footer>
}
