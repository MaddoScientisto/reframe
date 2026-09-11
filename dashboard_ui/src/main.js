import { createApp } from 'vue'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import App from './App.vue'
import './styles/base.css'

const queryClient = new QueryClient({
	defaultOptions: {
		queries: {
			staleTime: 30_000,
			gcTime: 10 * 60_000,
			retry: 1,
			refetchOnWindowFocus: false,
		},
	},
})

createApp(App)
	.use(VueQueryPlugin, { queryClient })
	.mount('#app')
