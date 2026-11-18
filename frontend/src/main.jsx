/** Bootstrap the React application and fail early if the HTML root is missing. */
import {StrictMode} from 'react'
import {createRoot} from 'react-dom/client'
import './index.css'
import App from './App.jsx'

const rootElement = document.getElementById("root");

if (!rootElement) {
    throw new Error('Could not find the root element with id "root".');
}

createRoot(rootElement).render(<StrictMode>
    <App/>
</StrictMode>,)
