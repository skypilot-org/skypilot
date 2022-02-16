var initToggleItems = () => {
  var itemsToToggle = document.querySelectorAll(togglebuttonSelector);
  console.log(itemsToToggle, togglebuttonSelector)
  // Add the button to each admonition and hook up a callback to toggle visibility
  itemsToToggle.forEach((item, index) => {
    var toggleID = `toggle-${index}`;
    var buttonID = `button-${toggleID}`;
    var collapseButton = `
      <button id="${buttonID}" class="toggle-button" data-target="${toggleID}" data-button="${buttonID}">
          <div class="bar horizontal" data-button="${buttonID}"></div>
          <div class="bar vertical" data-button="${buttonID}"></div>
      </button>`;

    item.setAttribute('id', toggleID);

    if (!item.classList.contains("toggle")){
      item.classList.add("toggle");
    }

    // If it's an admonition block, then we'll add the button inside
    if (item.classList.contains("admonition")) {
      item.insertAdjacentHTML("afterbegin", collapseButton);
    } else {
      item.insertAdjacentHTML('beforebegin', collapseButton);
    }

    thisButton = $(`#${buttonID}`);
    thisButton.on('click', toggleClickHandler);
    if (!item.classList.contains("toggle-shown")) {
      toggleHidden(thisButton[0]);
    }
  })
};

// This should simply add / remove the collapsed class and change the button text
var toggleHidden = (button) => {
  target = button.dataset['target']
  var itemToToggle = document.getElementById(target);
  if (itemToToggle.classList.contains("toggle-hidden")) {
    itemToToggle.classList.remove("toggle-hidden");
    button.classList.remove("toggle-button-hidden");
  } else {
    itemToToggle.classList.add("toggle-hidden");
    button.classList.add("toggle-button-hidden");
  }
}

var toggleClickHandler = (click) => {
  button = document.getElementById(click.target.dataset['button']);
  toggleHidden(button);
}

// If we want to blanket-add toggle classes to certain cells
var addToggleToSelector = () => {
  const selector = "";
  if (selector.length > 0) {
    document.querySelectorAll(selector).forEach((item) => {
      item.classList.add("toggle");
    })
  }
}

// Helper function to run when the DOM is finished
const sphinxToggleRunWhenDOMLoaded = cb => {
  if (document.readyState != 'loading') {
    cb()
  } else if (document.addEventListener) {
    document.addEventListener('DOMContentLoaded', cb)
  } else {
    document.attachEvent('onreadystatechange', function() {
      if (document.readyState == 'complete') cb()
    })
  }
}
sphinxToggleRunWhenDOMLoaded(addToggleToSelector)
sphinxToggleRunWhenDOMLoaded(initToggleItems)
