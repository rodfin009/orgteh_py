module.exports = {
  content: ["./templates/**/*.html", "./static/**/*.js"],
  darkMode: 'class',
  theme: {
    extend: {
      colors: { primary: '#7c3aed', darkbg: '#000000', cardbg: '#0d0d0d', lightcard: '#f5f3ff', success: '#22c55e' },
      fontFamily: { cairo: ['Cairo', 'sans-serif'] },
      zIndex: { '60':'60', '70':'70', '100':'100' }
    }
  },
  plugins: []
}
