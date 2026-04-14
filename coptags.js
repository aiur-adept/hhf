copy(
	JSON.stringify(
		Array.from(document.querySelectorAll('article ul'))
			.map(x => ({
				tag: x.querySelector('.me-1').innerText,
				cards: Array.from(x.querySelectorAll('a.text-body'))
					.slice(1).map(s => s.innerText)
			})),
		undefined,
		2)
	)