"use strict";
(() => {
  const panel=document.getElementById('feedbackImages'), problem=document.getElementById('feedbackHasProblem');
  const previews=document.getElementById('feedbackImagePreviews'), status=document.getElementById('feedbackImageStatus');
  const fields={problem:document.getElementById('feedbackImageProblem'),before:document.getElementById('feedbackImageBefore'),after:document.getElementById('feedbackImageAfter')};
  let images={}, errors={}, epoch=0, disabled=false;
  const pending=new Set(), versions={};
  const changed=()=>window.dispatchEvent(new Event('ece329:feedback-images-changed'));
  function draw() {
    previews.replaceChildren();
    for(const [role,data_url] of Object.entries(images)) {
      const figure=document.createElement('figure'), image=document.createElement('img'), caption=document.createElement('figcaption');
      image.src=data_url;image.alt={problem:'问题对话截图',before:'前文截图',after:'后文截图'}[role];
      const remove=document.createElement('button');remove.type='button';remove.className='ghost-button';remove.textContent='移除截图';remove.disabled=disabled;
      remove.addEventListener('click',()=>{if(disabled)return;delete images[role];delete errors[role];versions[role]={};fields[role].value='';draw();changed();});
      caption.textContent=image.alt;figure.append(image,caption,remove);previews.append(figure);
    }
  }
  async function compress(file) {
    if(file.size>12*1024*1024)throw Error('原图不能超过 12 MB。');
    const url=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('无法读取图片。'));reader.readAsDataURL(file);});
    const image=await new Promise((resolve,reject)=>{const value=new Image();value.onload=()=>resolve(value);value.onerror=()=>reject(Error('浏览器无法解析此图片，请转换成 JPG 或 PNG。'));value.src=url;});
    if(image.naturalWidth*image.naturalHeight>16_000_000)throw Error('图片像素过大，请缩小后上传。');
    const scale=Math.min(1,2000/image.naturalWidth,2000/image.naturalHeight), canvas=document.createElement('canvas');
    canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));canvas.height=Math.max(1,Math.round(image.naturalHeight*scale));
    const ctx=canvas.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0,canvas.width,canvas.height);
    const data=canvas.toDataURL('image/jpeg',.88);
    if(data.length>2*1024*1024*4/3)throw Error('压缩后图片仍超过 2 MB，请缩小后上传。');
    return data;
  }
  for(const [role,input] of Object.entries(fields)) input.addEventListener('change',async()=>{
    const generation=epoch, token={};versions[role]=token;pending.add(token);delete images[role];delete errors[role];draw();changed();
    status.textContent='正在处理截图……';
    try {if(input.files[0]) {const data=await compress(input.files[0]);if(generation===epoch && versions[role]===token)images[role]=data;}}
    catch(error){if(generation===epoch && versions[role]===token)errors[role]=error.message;}
    finally {pending.delete(token);if(generation===epoch && versions[role]===token){draw();status.textContent=Object.values(errors).join(' ');changed();}}
  });
  problem.addEventListener('change',changed);
  function show(category){panel.hidden=category!=='final_review';}
  window.ECE329FeedbackImages={
    show,
    restore(draft,category){epoch++;pending.clear();errors={};images={};for(const item of draft?.attachments || [])if(Object.hasOwn(fields,item.role)&&typeof item.data_url==='string')images[item.role]=item.data_url;problem.checked=draft?.has_problem===true;Object.values(fields).forEach(input=>input.value='');status.textContent='';draw();show(category);},
    payload(category){return category==='final_review'?{has_problem:problem.checked,attachments:Object.entries(images).map(([role,data_url])=>({role,data_url}))}:{};},
    disabled(value){disabled=value;problem.disabled=value;Object.values(fields).forEach(input=>input.disabled=value);draw();},
    validate(category){if(category!=='final_review')return true;
      if(pending.size){status.textContent='请等待截图处理完成。';return false;}
      if(Object.keys(errors).length){status.textContent=Object.values(errors).join(' ');return false;}
      if(problem.checked && Object.keys(images).length!==3){status.textContent='请上传问题对话、前文和后文三张截图。';return false;}
      return true;
    }
  };
})();
