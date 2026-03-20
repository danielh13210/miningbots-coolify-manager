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
