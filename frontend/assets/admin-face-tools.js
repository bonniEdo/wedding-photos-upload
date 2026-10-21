(() => {
  function create({api, status, getPeople, isLoggedIn, isBusy, setBusy, refresh, beginMerge, confirmAction}) {
    const el = id => document.getElementById(id);
    const reviewURLs = new Set(), editorURLs = new Set();
    let generation = 0, editorSequence = 0, nextOffset = null, reviewRevision = 0;
    let reloadSequence = 0;
    let editor = null, selectedFace = null;
    const revoke = urls => {for (const url of urls) URL.revokeObjectURL(url); urls.clear();};
    function button(text, handler) {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = text;
      b.onclick = handler; b.disabled = isBusy(); return b;
    }
    function text(tag, value, className) {
      const node = document.createElement(tag); node.textContent = value;
      if (className) node.className = className;
      return node;
    }
    async function secureImage(img, route, urls, currentGeneration, currentEditor=null) {
      try {
        const response = await WeddingAPI.request(route, {headers: {'X-Admin-Request': '1'}});
        if (!response.ok) throw new Error('crop unavailable');
        const blob = await response.blob();
        if (!isLoggedIn() || currentGeneration !== generation ||
            (currentEditor !== null && currentEditor !== editorSequence)) return;
        const url = URL.createObjectURL(blob); urls.add(url); img.src = url;
      } catch {img.alt = '裁切無法載入，請查看原照片';}
    }
    function faceImage(face, urls=reviewURLs, sequence=null) {
      const img = document.createElement('img'); img.alt = '待確認的人臉'; img.className = 'review-crop';
      secureImage(img, face.crop_url, urls, generation, sequence); return img;
    }
    async function act(route, body, message) {
      if (isBusy()) return;
      setBusy(true);
      try {
        const result = await api(route, body);
        closeEditor(); await refresh(); status(result.replayed ? '這次操作已完成，資料已重新整理。' : message);
      } catch (error) {
        status(error.message, true);
        if (el('faceEditor').open) el('faceEditorError').textContent = error.message;
        if (error.httpStatus === 409) {closeEditor(); await refresh().catch(() => {});}
      } finally {setBusy(false); syncBusy();}
    }
    function renderReview(review, revision) {
      const card = document.createElement('article'); card.className = 'review-card';
      card.append(text('h3', review.kind === 'duplicate' ? '可能是同一人' : '這張人臉待確認'));
      const samples = document.createElement('div'); samples.className = 'review-samples';
      for (const face of review.faces) {
        const sample = document.createElement('div'); sample.append(faceImage(face));
        sample.append(button('查看照片與人臉', () => openPhoto(face.photo_id, face.face_id)));
        samples.append(sample);
      }
      card.append(samples);
      if (review.same_photo) card.append(text('p', '這兩組曾出現在同張照片，請特別確認。', 'note'));
      const actions = document.createElement('div'); actions.className = 'actions';
      const route = `face-reviews/${encodeURIComponent(review.id)}/resolve`;
      if (review.kind === 'duplicate') {
        actions.append(button('合併這兩組', () => beginMerge(review.person_ids, revision)));
        actions.append(button('確認是不同人', () => act(route, {action:'different', revision}, '已記錄不同人，將排除此配對建議。')));
      } else {
        actions.append(button('確認目前人物正確', () => act(route, {action:'approve', revision}, '已確認這張人臉的歸屬。')));
      }
      actions.append(button('略過此建議', () => act(route, {action:'skip', revision}, '已略過，可從整理紀錄復原。')));
      card.append(actions); return card;
    }
    async function reload(append=false) {
      const request = ++reloadSequence;
      if (!append) {generation++; revoke(reviewURLs); el('faceReviews').replaceChildren();}
      const kind = el('reviewKind').value;
      const offset = append ? nextOffset : 0;
      if (offset === null) return;
      const data = await api(`face-reviews?kind=${kind}&offset=${offset}&limit=20`);
      if (!isLoggedIn() || request !== reloadSequence) return;
      // A new revision invalidates previously displayed decisions, including pagination.
      if (append && data.revision !== reviewRevision) return reload(false);
      reviewRevision = data.revision; nextOffset = data.next_offset;
      for (const review of data.reviews) el('faceReviews').append(renderReview(review, data.revision));
      el('reviewSummary').textContent = data.total ? `共 ${data.total} 項建議，請先查看照片再確認。` : '目前沒有這類待複核建議。';
      el('moreReviews').hidden = nextOffset === null;
      el('faceConstraints').replaceChildren();
      for (const constraint of data.constraints) {
        const row = document.createElement('div'); row.className = 'constraint-row';
        for (const face of constraint.faces) row.append(faceImage(face));
        row.append(text('span', '已確認不同人'));
        row.append(button('撤銷排除', () => act(`face-constraints/${encodeURIComponent(constraint.id)}/revoke`,
                    {revision:data.revision}, '已撤銷排除，符合條件時會再次顯示建議。')));
        el('faceConstraints').append(row);
      }
      if (!data.constraints.length) el('faceConstraints').append(text('p', '尚無不同人排除紀錄。', 'note'));
      syncBusy();
    }
    function syncEditor() {
      if (!editor || !selectedFace) return;
      el('moveFace').disabled = isBusy() || el('faceTarget').value === selectedFace.person_id;
      el('approveFace').hidden = !selectedFace.review_id;
      el('approveFace').disabled = isBusy();
      const target = getPeople().find(p => p.id === el('faceTarget').value);
      el('targetThumbnail').hidden = !target;
      if (target) el('targetThumbnail').src = WeddingAPI.url(target.thumbnail);
      el('faceSelection').textContent = `已選第 ${editor.faces.indexOf(selectedFace)+1} 張人臉；只會移動這張臉的歸屬。`;
      el('faceStage').querySelectorAll('button').forEach(b => b.classList.toggle('selected', b.dataset.face === selectedFace.face_id));
    }
    async function openPhoto(photoId, preferredId=null) {
      if (isBusy()) return;
      closeEditor(); const sequence = ++editorSequence;
      try {
        const data = await api(`photos/${encodeURIComponent(photoId)}/faces`);
        if (!isLoggedIn() || sequence !== editorSequence) return;
        editor = data; selectedFace = data.faces.find(f => f.face_id === preferredId) || data.faces[0];
        el('faceStage').replaceChildren(); el('faceChoices').replaceChildren(); el('faceEditorError').textContent = '';
        const full = document.createElement('img'); full.src = WeddingAPI.url(data.photo_url); full.alt = '照片預覽';
        el('faceStage').append(full);
        for (const [index, face] of data.faces.entries()) {
          const label = document.createElement('label'); label.className = 'face-choice';
          const radio = document.createElement('input'); radio.type = 'radio'; radio.name = 'face-pick';
          radio.value = face.face_id; radio.checked = face === selectedFace;
          const select = () => {selectedFace = face; radio.checked = true; el('faceTarget').value = face.person_id; syncEditor();};
          radio.onchange = select;
          label.append(radio, faceImage(face, editorURLs, sequence), text('span', `第 ${index+1} 張人臉${face.confirmed ? ' · 已確認' : ''}`));
          el('faceChoices').append(label);
          if (face.normalized_bbox) {
            const [x,y,w,h] = face.normalized_bbox;
            const box = button(String(index+1), select); box.className = 'face-box'; box.dataset.face = face.face_id;
            box.setAttribute('aria-label', `選擇照片中的第 ${index+1} 張人臉`);
            const left = Math.max(0,x), top = Math.max(0,y);
            box.style.left = `${left*100}%`; box.style.top = `${top*100}%`;
            box.style.width = `${Math.max(0,Math.min(1,x+w)-left)*100}%`;
            box.style.height = `${Math.max(0,Math.min(1,y+h)-top)*100}%`; el('faceStage').append(box);
          }
        }
        el('faceTarget').replaceChildren(new Option('拆出成新人物', ''));
        getPeople().forEach((person,index) => el('faceTarget').append(new Option(`人物 ${index+1} · ${person.count} 張照片`, person.id)));
        el('faceTarget').value = selectedFace?.person_id || '';
        el('faceControls').hidden = !selectedFace;
        syncEditor(); el('faceEditor').showModal();
      } catch (error) {status(error.message, true);}
    }
    function closeEditor() {
      editorSequence++; editor = selectedFace = null; revoke(editorURLs);
      if (el('faceEditor').open) el('faceEditor').close();
    }
    function reset() {
      generation++; reloadSequence++; closeEditor(); revoke(reviewURLs);
      el('faceReviews').replaceChildren(); el('faceConstraints').replaceChildren();
    }
    function syncBusy() {
      document.querySelectorAll('#reviewPanel button,#reviewPanel select,#faceEditor button,#faceEditor select,#faceEditor input')
        .forEach(node => node.disabled = isBusy());
      syncEditor();
    }
    el('reviewKind').onchange = () => reload().catch(error => status(error.message, true));
    el('refreshReviews').onclick = () => reload().catch(error => status(error.message, true));
    el('moreReviews').onclick = () => reload(true).catch(error => status(error.message, true));
    el('closeFaceEditor').onclick = closeEditor;
    el('faceEditor').addEventListener('cancel', event => {if (isBusy()) event.preventDefault(); else closeEditor();});
    el('faceTarget').onchange = syncEditor;
    el('moveFace').onclick = async () => {
      if (!editor || !selectedFace || isBusy()) return;
      const target = el('faceTarget').value;
      const snapshot = editor, face = selectedFace;
      if (!await confirmAction(target ? '將選定人臉移到目標人物？同張照片的其他人臉會保持原歸屬。' : '將選定人臉拆出成新人物？')) return;
      if (editor !== snapshot || selectedFace !== face || !isLoggedIn() || isBusy()) return;
      act('faces/reassign', {face_ids:[selectedFace.face_id], target_id:target || null, revision:editor.revision}, '已更新人臉歸屬，可從整理紀錄復原。');
    };
    el('approveFace').onclick = () => {
      if (editor && selectedFace?.review_id) act(`face-reviews/${selectedFace.review_id}/resolve`,
            {action:'approve', revision:editor.revision}, '已確認這張人臉的歸屬。');
    };
    return {reload, openPhoto, reset, closeEditor, syncBusy};
  }
  window.WeddingFaceTools = {create};
})();
