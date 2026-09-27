import './globals.css'

export const metadata = {
  title: 'DevOnboard AI',
  description: 'Evidence-grounded code exploration',
}

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
