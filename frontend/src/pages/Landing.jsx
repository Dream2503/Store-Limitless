import {useState} from "react";
import {Link} from "react-router-dom";

const features = [{
    icon: "upload_file",
    title: "Straightforward uploads",
    description: "Add files through the upload interface and follow their status as the work progresses.",
}, {
    icon: "folder_open",
    title: "Files and folders",
    description: "Keep content organized with directories, breadcrumbs, and familiar file-management actions.",
}, {
    icon: "download", title: "Access your files", description: "Browse stored content and download files when you need them.",
}, {
    icon: "movie", title: "Supported media previews", description: "Play supported video files in the built-in viewer without leaving the app.",
}, {
    icon: "manage_search", title: "Find things faster", description: "Search across your files and move through folders without losing your place.",
}, {
    icon: "shield_lock",
    title: "Account-based access",
    description: "Sign in to use the protected dashboard and the file tools associated with your account.",
},];

const steps = [{
    number: "01", icon: "person_add", title: "Create your account", description: "Register through the existing StoreLimitless sign-up flow."
}, {
    number: "02", icon: "cloud_upload", title: "Upload your files", description: "Add files from the upload interface and keep an eye on progress."
}, {
    number: "03", icon: "folder_copy", title: "Organize your space", description: "Create folders, search your content, and manage your files."
}, {
    number: "04", icon: "play_circle", title: "Get to your content", description: "Open, download, or preview supported media from your dashboard."
},];

function Brand({light = false}) {
    return (<Link to="/"
                  className={`inline-flex items-center gap-2.5 rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#3ecf8e] ${light ? "text-[#f5f7f6]" : "text-white"}`}
                  aria-label="StoreLimitless home">
            <span
                className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#3ecf8e]/30 bg-[#3ecf8e]/10 text-[#3ecf8e] shadow-[0_0_30px_rgba(62,207,142,0.08)]">
                <span className="material-symbols-outlined text-[22px]" aria-hidden="true">cloud</span>
            </span>
        <span className="text-[15px] font-semibold tracking-tight">Store<span className="text-[#3ecf8e]">Limitless</span></span>
    </Link>);
}

function ProductPreview() {
    return (<div className="relative mx-auto w-full max-w-[620px]">
        <div
            className="pointer-events-none absolute -inset-8 rounded-[2.5rem] bg-[radial-gradient(ellipse_at_center,rgba(62,207,142,0.16),transparent_68%)] blur-2xl"
            aria-hidden="true"/>
        <div
            className="relative rotate-0 overflow-hidden rounded-2xl border border-white/10 bg-[#101413] shadow-[0_30px_100px_rgba(0,0,0,0.42)] sm:rotate-[0.5deg]">
            <div className="flex items-center justify-between border-b border-white/[0.08] bg-white/[0.025] px-4 py-3 sm:px-5">
                <div className="flex items-center gap-2.5">
                    <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#3ecf8e]/10 text-[#3ecf8e]"><span
                        className="material-symbols-outlined text-[17px]" aria-hidden="true">cloud</span></div>
                    <span className="text-xs font-semibold text-white">Workspace</span>
                    <span
                        className="hidden rounded-md border border-white/10 px-1.5 py-0.5 text-[10px] text-[#8e9994] sm:inline">Illustrative preview</span>
                </div>
                <span className="material-symbols-outlined text-[18px] text-[#77817c]" aria-hidden="true">more_horiz</span>
            </div>
            <div className="grid min-h-[310px] grid-cols-[112px_minmax(0,1fr)] sm:grid-cols-[155px_minmax(0,1fr)]">
                <aside className="border-r border-white/[0.07] bg-black/10 p-2.5 sm:p-3.5">
                    <p className="mb-3 px-2 text-[9px] font-semibold uppercase tracking-[0.16em] text-[#68746e] sm:text-[10px]">Workspace</p>
                    <div className="flex items-center gap-2 rounded-lg bg-[#3ecf8e]/10 px-2 py-2 text-[10px] text-[#6de5ae] sm:text-xs"><span
                        className="material-symbols-outlined text-[15px]" aria-hidden="true">folder_open</span>All files
                    </div>
                    <div className="mt-1 flex items-center gap-2 rounded-lg px-2 py-2 text-[10px] text-[#7d8983] sm:text-xs"><span
                        className="material-symbols-outlined text-[15px]" aria-hidden="true">delete</span>Trash
                    </div>
                    <div className="mt-6 border-t border-white/[0.07] pt-4">
                        <p className="mb-3 px-2 text-[9px] font-semibold uppercase tracking-[0.16em] text-[#68746e] sm:text-[10px]">Quick view</p>
                        <div className="mb-2 flex items-center gap-2 px-2 text-[10px] text-[#89958f] sm:text-xs"><span
                            className="material-symbols-outlined text-[15px]" aria-hidden="true">image</span>Images
                        </div>
                        <div className="mb-2 flex items-center gap-2 px-2 text-[10px] text-[#89958f] sm:text-xs"><span
                            className="material-symbols-outlined text-[15px]" aria-hidden="true">movie</span>Videos
                        </div>
                    </div>
                </aside>
                <div className="min-w-0 p-3 sm:p-5">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div><p className="text-[10px] text-[#78847e] sm:text-xs">Your workspace</p><h3
                            className="mt-1 text-sm font-semibold tracking-tight text-white sm:text-base">All files</h3></div>
                        <span
                            className="inline-flex items-center gap-1.5 rounded-lg bg-[#3ecf8e] px-2.5 py-2 text-[10px] font-semibold text-[#07150e] sm:px-3 sm:text-xs"><span
                            className="material-symbols-outlined text-[15px]" aria-hidden="true">add</span>Upload files</span>
                    </div>
                    <div className="mt-4 grid grid-cols-2 gap-2 sm:mt-5 sm:gap-3">
                        <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-2.5 sm:p-3">
                            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#e5b567]/10 text-[#e5b567]"><span
                                className="material-symbols-outlined text-[18px]" aria-hidden="true">folder</span></div>
                            <p className="mt-2.5 truncate text-[10px] font-medium text-[#e7ebe8] sm:text-xs">Projects</p><p
                            className="mt-1 text-[9px] text-[#718078] sm:text-[10px]">Folder</p></div>
                        <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-2.5 sm:p-3">
                            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#79baff]/10 text-[#79baff]"><span
                                className="material-symbols-outlined text-[18px]" aria-hidden="true">folder</span></div>
                            <p className="mt-2.5 truncate text-[10px] font-medium text-[#e7ebe8] sm:text-xs">Media</p><p
                            className="mt-1 text-[9px] text-[#718078] sm:text-[10px]">Folder</p></div>
                    </div>
                    <div className="mt-5">
                        <div
                            className="mb-2 grid grid-cols-[minmax(0,1fr)_auto] border-b border-white/[0.07] pb-2 text-[9px] uppercase tracking-[0.12em] text-[#647169] sm:grid-cols-[minmax(0,1fr)_90px_70px] sm:text-[10px]">
                            <span>Name</span><span className="hidden sm:block">Type</span><span className="text-right">Status</span></div>
                        {[{
                            icon: "picture_as_pdf", color: "text-[#f28b82]", name: "Project-notes.pdf", type: "PDF", status: "Ready"
                        }, {icon: "movie", color: "text-[#82b5ff]", name: "Product-demo.mp4", type: "Video", status: "Ready"}, {
                            icon: "image", color: "text-[#3ecf8e]", name: "Workspace.png", type: "Image", status: "Ready"
                        },].map((item) => <div key={item.name}
                                               className="grid grid-cols-[minmax(0,1fr)_auto] items-center border-b border-white/[0.045] py-2.5 sm:grid-cols-[minmax(0,1fr)_90px_70px]">
                            <div className="flex min-w-0 items-center gap-2"><span
                                className={`material-symbols-outlined text-[18px] ${item.color}`} aria-hidden="true">{item.icon}</span><span
                                className="truncate text-[10px] text-[#c4ccc7] sm:text-xs">{item.name}</span></div>
                            <span className="hidden text-[10px] text-[#78847e] sm:block">{item.type}</span><span
                            className="flex items-center justify-end gap-1 text-[9px] text-[#80d5ac] sm:text-[10px]"><span
                            className="h-1.5 w-1.5 rounded-full bg-[#3ecf8e]"/>{item.status}</span>
                        </div>)}
                    </div>
                    <div className="mt-4 flex items-center gap-2 rounded-lg border border-[#3ecf8e]/15 bg-[#3ecf8e]/[0.055] p-2.5 sm:p-3"><span
                        className="material-symbols-outlined text-[17px] text-[#3ecf8e]" aria-hidden="true">info</span><p
                        className="text-[9px] leading-relaxed text-[#99aaa0] sm:text-[10px]">A visual preview of the interface. File names and
                        statuses shown here are examples.</p></div>
                </div>
            </div>
        </div>
        <div
            className="absolute -bottom-5 -left-2 hidden items-center gap-2 rounded-xl border border-white/10 bg-[#171d19]/95 px-3 py-2.5 shadow-xl backdrop-blur sm:flex">
                <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#3ecf8e]/10 text-[#3ecf8e]"><span
                    className="material-symbols-outlined text-[16px]" aria-hidden="true">task_alt</span></span>
            <div><p className="text-[11px] font-medium text-white">Simple by design</p><p className="mt-0.5 text-[10px] text-[#819087]">Your
                files, organized your way</p></div>
        </div>
    </div>);
}

export default function Landing() {
    const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
    const closeMenu = () => setMobileMenuOpen(false);

    return (<div
        className="min-h-screen scroll-smooth overflow-x-hidden bg-[#0b0e0c] text-[#f3f7f4] selection:bg-[#3ecf8e]/30 selection:text-white motion-reduce:scroll-auto"
        style={{fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"}}>
        <a href="#main-content"
           className="sr-only z-[100] rounded-md bg-[#3ecf8e] px-4 py-2 font-medium text-black focus:not-sr-only focus:fixed focus:left-4 focus:top-4">Skip
            to content</a>
        <header className="fixed inset-x-0 top-0 z-50 border-b border-white/[0.07] bg-[#0b0e0c]/85 backdrop-blur-xl">
            <div className="mx-auto flex h-[72px] max-w-7xl items-center justify-between px-5 sm:px-8 lg:px-10">
                <Brand/>
                <nav className="hidden items-center gap-8 md:flex" aria-label="Main navigation">
                    <a href="#home"
                       className="rounded-sm text-sm text-[#a6b0aa] transition-colors hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#3ecf8e]">Home</a>
                    <a href="#features"
                       className="rounded-sm text-sm text-[#a6b0aa] transition-colors hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#3ecf8e]">Features</a>
                    <a href="#how-it-works"
                       className="rounded-sm text-sm text-[#a6b0aa] transition-colors hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#3ecf8e]">How
                        it works</a>
                    <a href="https://github.com/Dream2503" target="_blank" rel="noopener noreferrer"
                       className="inline-flex items-center gap-1.5 rounded-sm text-sm text-[#a6b0aa] transition-colors hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#3ecf8e]"><span
                        className="material-symbols-outlined text-[17px]" aria-hidden="true">code</span>GitHub</a>
                </nav>
                <div className="hidden items-center gap-3 md:flex">
                    <Link to="/login"
                          className="rounded-lg px-4 py-2.5 text-sm font-medium text-[#c3ccc6] transition-colors hover:bg-white/[0.05] hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3ecf8e]">Log
                        in</Link>
                    <Link to="/signup"
                          className="inline-flex items-center gap-2 rounded-lg bg-[#3ecf8e] px-4 py-2.5 text-sm font-semibold text-[#07130d] shadow-[0_0_24px_rgba(62,207,142,0.10)] transition hover:bg-[#63dba6] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3ecf8e]">Get
                        started <span className="material-symbols-outlined text-[17px]" aria-hidden="true">arrow_outward</span></Link>
                </div>
                <button type="button"
                        className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-white/10 text-[#e2e8e4] transition-colors hover:border-white/20 hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3ecf8e] md:hidden"
                        onClick={() => setMobileMenuOpen((open) => !open)}
                        aria-label={mobileMenuOpen ? "Close navigation menu" : "Open navigation menu"} aria-expanded={mobileMenuOpen}
                        aria-controls="mobile-navigation"><span className="material-symbols-outlined"
                                                                aria-hidden="true">{mobileMenuOpen ? "close" : "menu"}</span></button>
            </div>
            {mobileMenuOpen && <nav id="mobile-navigation" className="border-t border-white/[0.07] bg-[#101411] px-5 py-4 md:hidden"
                                    aria-label="Mobile navigation">
                <div className="mx-auto flex max-w-7xl flex-col gap-1">
                    <a onClick={closeMenu} href="#home"
                       className="rounded-lg px-3 py-3 text-sm text-[#bac4be] transition-colors hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[#3ecf8e]">Home</a>
                    <a onClick={closeMenu} href="#features"
                       className="rounded-lg px-3 py-3 text-sm text-[#bac4be] transition-colors hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[#3ecf8e]">Features</a>
                    <a onClick={closeMenu} href="#how-it-works"
                       className="rounded-lg px-3 py-3 text-sm text-[#bac4be] transition-colors hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[#3ecf8e]">How
                        it works</a>
                    <a onClick={closeMenu} href="https://github.com/Dream2503" target="_blank" rel="noopener noreferrer"
                       className="rounded-lg px-3 py-3 text-sm text-[#bac4be] transition-colors hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[#3ecf8e]">GitHub
                        profile ↗</a>
                    <div className="mt-2 grid grid-cols-2 gap-2 border-t border-white/[0.07] pt-4"><Link onClick={closeMenu} to="/login"
                                                                                                         className="rounded-lg border border-white/10 px-3 py-3 text-center text-sm font-medium text-white transition-colors hover:border-white/20 hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3ecf8e]">Log
                        in</Link><Link onClick={closeMenu} to="/signup"
                                       className="rounded-lg bg-[#3ecf8e] px-3 py-3 text-center text-sm font-semibold text-[#07130d] transition-colors hover:bg-[#66dca8] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3ecf8e]">Get
                        started</Link></div>
                </div>
            </nav>}
        </header>

        <main id="main-content" className="pt-[72px]">
            <section id="home" className="relative isolate scroll-mt-24">
                <div className="pointer-events-none absolute inset-0 -z-10 overflow-hidden" aria-hidden="true">
                    <div className="absolute left-[-10%] top-[-15rem] h-[34rem] w-[34rem] rounded-full bg-[#1a7751]/15 blur-[120px]"/>
                    <div className="absolute right-[-8%] top-20 h-[32rem] w-[32rem] rounded-full bg-[#214f77]/10 blur-[130px]"/>
                    <div
                        className="absolute inset-0 opacity-[0.11] [background-image:linear-gradient(rgba(255,255,255,0.10)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.10)_1px,transparent_1px)] [background-size:64px_64px] [mask-image:linear-gradient(to_bottom,black,transparent_88%)]"/>
                </div>
                <div
                    className="mx-auto grid max-w-7xl items-center gap-14 px-5 pb-24 pt-16 sm:px-8 sm:pb-28 sm:pt-24 lg:grid-cols-[0.92fr_1.08fr] lg:gap-12 lg:px-10 lg:pb-32 lg:pt-28">
                    <div className="relative z-10 max-w-2xl">
                        <div
                            className="mb-7 inline-flex items-center gap-2 rounded-full border border-[#3ecf8e]/20 bg-[#3ecf8e]/[0.07] px-3 py-1.5 text-xs font-medium text-[#95e6bd]">
                            <span className="h-1.5 w-1.5 rounded-full bg-[#3ecf8e] shadow-[0_0_12px_rgba(62,207,142,0.8)]"/>A clearer way to
                            manage your files
                        </div>
                        <h1 className="max-w-[680px] text-[2.8rem] font-semibold leading-[1.06] tracking-[-0.055em] text-white sm:text-6xl lg:text-[4.35rem]">Your
                            files.<br/>Your space.<br/><span
                                className="bg-gradient-to-r from-[#3ecf8e] via-[#7ce6b2] to-[#9ac8ff] bg-clip-text text-transparent">Your control.</span>
                        </h1>
                        <p className="mt-7 max-w-xl text-base leading-7 text-[#a3afa7] sm:text-lg sm:leading-8">StoreLimitless gives you a
                            straightforward place to upload, organize, find, and access your files through one clean interface.</p>
                        <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
                            <Link to="/signup"
                                  className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-[#3ecf8e] px-5 py-3 text-sm font-semibold text-[#07130d] shadow-[0_7px_30px_rgba(62,207,142,0.13)] transition duration-200 hover:-translate-y-0.5 hover:bg-[#66dca8] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3ecf8e]">Get
                                started <span className="material-symbols-outlined text-[18px]" aria-hidden="true">arrow_forward</span></Link>
                            <Link to="/login"
                                  className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg border border-white/10 bg-white/[0.025] px-5 py-3 text-sm font-medium text-[#e4eae6] transition duration-200 hover:border-white/20 hover:bg-white/[0.06] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3ecf8e] motion-reduce:transition-none">Log
                                in to your account <span className="material-symbols-outlined text-[17px]" aria-hidden="true">login</span></Link>
                        </div>
                        <div className="mt-9 flex flex-wrap items-center gap-x-5 gap-y-3 text-xs text-[#87938b]"><span
                            className="inline-flex items-center gap-2"><span className="material-symbols-outlined text-[17px] text-[#3ecf8e]"
                                                                             aria-hidden="true">folder_open</span>Organize files and folders</span><span
                            className="inline-flex items-center gap-2"><span className="material-symbols-outlined text-[17px] text-[#3ecf8e]"
                                                                             aria-hidden="true">play_circle</span>Preview supported media</span>
                        </div>
                    </div>
                    <div className="relative pt-2 lg:pt-0"><ProductPreview/></div>
                </div>
            </section>

            <section className="border-y border-white/[0.07] bg-white/[0.018]">
                <div
                    className="mx-auto flex max-w-7xl flex-col gap-4 px-5 py-6 text-center sm:px-8 md:flex-row md:items-center md:justify-between md:px-10 md:text-left">
                    <p className="text-sm font-medium text-[#c7d0ca]">Made for the everyday work of managing files.</p>
                    <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-3 text-xs text-[#849088] md:justify-end"><span
                        className="inline-flex items-center gap-2"><span className="material-symbols-outlined text-[16px] text-[#3ecf8e]"
                                                                         aria-hidden="true">upload</span>Upload and track</span><span
                        className="inline-flex items-center gap-2"><span className="material-symbols-outlined text-[16px] text-[#3ecf8e]"
                                                                         aria-hidden="true">drive_file_move</span>Organize content</span><span
                        className="inline-flex items-center gap-2"><span className="material-symbols-outlined text-[16px] text-[#3ecf8e]"
                                                                         aria-hidden="true">search</span>Find files</span></div>
                </div>
            </section>

            <section id="features" className="scroll-mt-24 px-5 py-20 sm:px-8 sm:py-24 lg:px-10 lg:py-28">
                <div className="mx-auto max-w-7xl">
                    <div className="mx-auto max-w-2xl text-center"><p
                        className="text-xs font-semibold uppercase tracking-[0.2em] text-[#3ecf8e]">Features</p><h2
                        className="mt-4 text-3xl font-semibold tracking-[-0.04em] text-white sm:text-4xl">Everything you need to keep files in
                        order.</h2><p className="mt-4 text-sm leading-7 text-[#96a29a] sm:text-base">Useful tools, a clear workspace, and fewer
                        obstacles between you and the files you need.</p></div>
                    <div className="mt-12 grid gap-3 sm:grid-cols-2 lg:mt-16 lg:grid-cols-3">
                        {features.map((feature) => <article key={feature.title}
                                                            className="group relative overflow-hidden rounded-2xl border border-[#26332b] bg-[#101512] p-6 shadow-[0_12px_36px_rgba(0,0,0,0.12)] transition duration-200 hover:-translate-y-1 hover:border-[#3ecf8e]/30 hover:bg-[#131a16] hover:shadow-[0_18px_44px_rgba(0,0,0,0.20)] sm:p-7 motion-reduce:transform-none motion-reduce:transition-none">
                            <div
                                className="absolute right-0 top-0 h-24 w-24 rounded-full bg-[#3ecf8e]/[0.035] blur-2xl transition group-hover:bg-[#3ecf8e]/[0.09]"
                                aria-hidden="true"/>
                            <div
                                className="relative flex h-11 w-11 items-center justify-center rounded-xl border border-[#3ecf8e]/15 bg-[#3ecf8e]/[0.08] text-[#58d99f]">
                                <span className="material-symbols-outlined text-[22px]" aria-hidden="true">{feature.icon}</span></div>
                            <h3 className="relative mt-5 text-base font-semibold text-[#edf3ef]">{feature.title}</h3><p
                            className="relative mt-2.5 text-sm leading-6 text-[#8f9b93]">{feature.description}</p>
                            <div className="relative mt-5 h-px w-10 bg-[#3ecf8e]/35 transition-all group-hover:w-16 group-hover:bg-[#3ecf8e]/80"
                                 aria-hidden="true"/>
                        </article>)}
                    </div>
                </div>
            </section>

            <section id="how-it-works"
                     className="scroll-mt-24 border-y border-white/[0.07] bg-[#0f1310] px-5 py-20 sm:px-8 sm:py-24 lg:px-10 lg:py-28">
                <div className="mx-auto max-w-7xl">
                    <div className="max-w-2xl"><p className="text-xs font-semibold uppercase tracking-[0.2em] text-[#3ecf8e]">How it works</p><h2
                        className="mt-4 text-3xl font-semibold tracking-[-0.04em] text-white sm:text-4xl">Up and running in a few steps.</h2><p
                        className="mt-4 text-sm leading-7 text-[#96a29a] sm:text-base">Start with an account, then use the same workspace to
                        upload, organize, and access your content.</p></div>
                    <div className="mt-12 grid gap-8 sm:grid-cols-2 lg:mt-16 lg:grid-cols-4 lg:gap-5">
                        {steps.map((step, index) => <article key={step.number}
                                                             className="relative border-l border-white/10 pl-5 sm:pl-6 lg:border-l-0 lg:border-t lg:pl-0 lg:pt-6">
                            <div
                                className="absolute -left-[5px] top-0 h-2.5 w-2.5 rounded-full border-2 border-[#0f1310] bg-[#3ecf8e] lg:-top-[5px] lg:left-0"
                                aria-hidden="true"/>
                            <div className="flex items-center gap-3"><span className="font-mono text-xs text-[#3ecf8e]">{step.number}</span><span
                                className="material-symbols-outlined text-[20px] text-[#819088]" aria-hidden="true">{step.icon}</span></div>
                            <h3 className="mt-4 text-base font-semibold text-white">{step.title}</h3><p
                            className="mt-2 text-sm leading-6 text-[#8f9b93]">{step.description}</p>
                            {index < steps.length - 1 &&
                                <span className="absolute right-0 top-[-1.9rem] hidden text-[#405047] lg:block" aria-hidden="true">→</span>}
                        </article>)}
                    </div>
                </div>
            </section>

            <section className="px-5 py-20 sm:px-8 sm:py-24 lg:px-10 lg:py-28">
                <div
                    className="relative mx-auto max-w-7xl overflow-hidden rounded-3xl border border-[#3ecf8e]/20 bg-[#101813] px-6 py-12 text-center sm:px-12 sm:py-16 lg:px-20">
                    <div
                        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,rgba(62,207,142,0.13),transparent_60%)]"
                        aria-hidden="true"/>
                    <div
                        className="pointer-events-none absolute inset-0 opacity-[0.12] [background-image:linear-gradient(rgba(255,255,255,0.08)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.08)_1px,transparent_1px)] [background-size:42px_42px]"
                        aria-hidden="true"/>
                    <div className="relative mx-auto max-w-2xl"><span
                        className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl border border-[#3ecf8e]/20 bg-[#3ecf8e]/10 text-[#3ecf8e]"><span
                        className="material-symbols-outlined text-[25px]" aria-hidden="true">cloud_done</span></span><h2
                        className="mt-6 text-3xl font-semibold tracking-[-0.04em] text-white sm:text-4xl">Ready to take control of your
                        files?</h2><p className="mx-auto mt-4 max-w-xl text-sm leading-7 text-[#9ba9a0] sm:text-base">Get started with
                        StoreLimitless and make managing your files simpler.</p>
                        <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row"><Link to="/signup"
                                                                                                   className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-[#3ecf8e] px-5 py-3 text-sm font-semibold text-[#07130d] transition duration-200 hover:bg-[#66dca8] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3ecf8e] motion-reduce:transition-none">Create
                            an account <span className="material-symbols-outlined text-[17px]" aria-hidden="true">arrow_forward</span></Link><Link
                            to="/login"
                            className="inline-flex min-h-12 items-center justify-center rounded-lg border border-white/10 px-5 py-3 text-sm font-medium text-white transition duration-200 hover:border-white/20 hover:bg-white/[0.06] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3ecf8e] motion-reduce:transition-none">I
                            already have an account</Link></div>
                    </div>
                </div>
            </section>
        </main>

        <footer className="border-t border-white/[0.08] bg-[#090c0a] px-5 py-8 sm:px-8 lg:px-10">
            <div className="mx-auto flex max-w-7xl flex-col gap-7 sm:flex-row sm:items-center sm:justify-between">
                <div><Brand/><p className="mt-3 max-w-sm text-xs leading-6 text-[#768279]">A clean workspace to upload, organize, and access your
                    files.</p></div>
                <div className="flex flex-wrap items-center gap-x-6 gap-y-3 text-xs text-[#98a39b]"><a href="#features"
                                                                                                       className="rounded-sm transition hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#3ecf8e]">Features</a><a
                    href="#how-it-works"
                    className="rounded-sm transition hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#3ecf8e]">How
                    it works</a><Link to="/login"
                                      className="rounded-sm transition hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#3ecf8e]">Log
                    in</Link><a href="https://github.com/Dream2503" target="_blank" rel="noopener noreferrer"
                                className="inline-flex items-center gap-1.5 rounded-sm transition hover:text-[#3ecf8e] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#3ecf8e]"><span
                    className="material-symbols-outlined text-[16px]" aria-hidden="true">code</span>GitHub profile</a></div>
            </div>
            <div
                className="mx-auto mt-7 max-w-7xl border-t border-white/[0.06] pt-5 text-[11px] text-[#77837a]">© {new Date().getFullYear()} StoreLimitless.
                All rights reserved.
            </div>
        </footer>
    </div>);
}
