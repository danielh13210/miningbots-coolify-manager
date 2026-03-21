function isFormEmpty(form) {
    for(const node of document.querySelectorAll("input, textarea")){
        if(node.type!="hidden" && node.type!="submit" && node.type!="checkbox" && !node.disabled){
            if(node.value!=""){
                return false;
            }
        }
    }
    return true;
}

function beforeunload(form){
    if(form.isSubmitting)return false; // skip the dialog if submitting
    if(!isFormEmpty(form)) return true;
    else return false;
}

function attachSubmitButton(form){
    form.isSubmitting=false;
    form.addEventListener("submit", function() {
        form.isSubmitting = true;
    });
    window.addEventListener("beforeunload",(e)=>{
        if(beforeunload(form)){
            e.returnValue='dialog';
        };
    });
}
