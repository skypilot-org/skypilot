var scrollToActive=()=>{var navbar=document.getElementById('site-navigation')
var active_pages=navbar.querySelectorAll(".active")
var active_page=active_pages[active_pages.length-1]
if(active_page!==undefined&&active_page.offsetTop>($(window).height()*.5)){navbar.scrollTop=active_page.offsetTop-($(window).height()*.2)}}
var sbRunWhenDOMLoaded=cb=>{if(document.readyState!='loading'){cb()}else if(document.addEventListener){document.addEventListener('DOMContentLoaded',cb)}else{document.attachEvent('onreadystatechange',function(){if(document.readyState=='complete')cb()})}}
function toggleFullScreen(){var navToggler=$("#navbar-toggler");if(!document.fullscreenElement){document.documentElement.requestFullscreen();if(!navToggler.hasClass("collapsed")){navToggler.click();}}else{if(document.exitFullscreen){document.exitFullscreen();if(navToggler.hasClass("collapsed")){navToggler.click();}}}}
var initTooltips=()=>{$(document).ready(function(){$('[data-toggle="tooltip"]').tooltip();});}
var initTocHide=()=>{var onScreenItems=[];let hideTocCallback=(entries,observer)=>{entries.forEach((entry)=>{if(entry.isIntersecting){onScreenItems.push(entry.target);}else{for(let ii=0;ii<onScreenItems.length;ii++){if(onScreenItems[ii]===entry.target){onScreenItems.splice(ii,1);break}}};});if(onScreenItems.length>0){$("div.bd-toc").removeClass("show")}else{$("div.bd-toc").addClass("show")}};let manageScrolledClassOnBody=(entries,observer)=>{if(entries[0].boundingClientRect.y<0){document.body.classList.add("scrolled");}else{document.body.classList.remove("scrolled");}}
let tocObserver=new IntersectionObserver(hideTocCallback);const selectorClasses=["margin","margin-caption","full-width","sidebar","popout"];marginSelector=[]
selectorClasses.forEach((ii)=>{marginSelector.push(...[`.${ii}`,`.tag_${ii}`,`.${ii.replace("-", "_")}`])});document.querySelectorAll(marginSelector.join(", ")).forEach((ii)=>{tocObserver.observe(ii);});let scrollObserver=new IntersectionObserver(manageScrolledClassOnBody);scrollObserver.observe(document.querySelector(".sbt-scroll-pixel-helper"));}
var printPdf=(el)=>{let tooltipID=$(el).attr("aria-describedby")
let tooltipTextDiv=$("#"+tooltipID).detach()
window.print()
$("body").append(tooltipTextDiv)}
var initThebeSBT=()=>{var title=$("div.section h1")[0]
if(!$(title).next().hasClass("thebe-launch-button")){$("<button class='thebe-launch-button'></button>").insertAfter($(title))}
initThebe();}
sbRunWhenDOMLoaded(initTooltips)
sbRunWhenDOMLoaded(scrollToActive)
sbRunWhenDOMLoaded(initTocHide)
