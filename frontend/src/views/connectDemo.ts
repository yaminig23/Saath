/* Sample data for the Connect + Nearby demo experience. Every person,
group, and volunteer here is fictional. Screens using this data always
carry a demo banner, and no verification or rating is ever claimed. */

export interface DemoGroup {
  id: string;
  name: string;
  blurb: string;
  members: number;
  meetup: string;
  announcement: string;
}

export interface DemoMessage {
  id: string;
  author: string;
  text: string;
  mine: boolean;
}

export interface DemoProfile {
  id: string;
  name: string;
  interests: string[];
  languages: string[];
  note: string;
  greeting: string;
}

export interface DemoVolunteer {
  id: string;
  name: string;
  skills: string[];
  languages: string[];
  availability: string;
  about: string;
}

export interface DemoRequest {
  id: string;
  volunteerId: string;
  volunteerName: string;
  need: string;
  status: "sent" | "cancelled";
  created: number;
}

export const GROUPS: DemoGroup[] = [
  { id: "reading", name: "Reading Circle", blurb: "A book a month, zero pressure", members: 128, meetup: "Sat 10:00 UTC · Monthly book chat", announcement: "April's book is a collection of short stories — join any time, no need to finish first." },
  { id: "cooking", name: "Home Cooking", blurb: "Weeknight recipes that work", members: 312, meetup: "Wed 17:00 UTC · Cook-along: lentil soup", announcement: "This week's cook-along needs only a pot, lentils, and an onion." },
  { id: "walks", name: "Morning Walks", blurb: "Gentle walks, good company", members: 86, meetup: "Daily 06:30 local · Park gate", announcement: "Sunday's walk ends at the lake café — all paces welcome." },
  { id: "music", name: "Music Lovers", blurb: "Share what you're hearing", members: 245, meetup: "Fri 19:00 UTC · Listening room", announcement: "Friday's theme is songs our grandparents loved." },
  { id: "gardening", name: "Gardening Club", blurb: "Balconies and backyards", members: 97, meetup: "Sun 09:00 UTC · Seed swap", announcement: "Bring spare seeds on Sunday — beginners get a starter kit." },
  { id: "movies", name: "Movie Nights", blurb: "Watch together, discuss after", members: 189, meetup: "Sat 18:00 UTC · Comedy night", announcement: "Vote for Saturday's comedy before Thursday." },
  { id: "language", name: "Language Exchange", blurb: "Practise over tea", members: 154, meetup: "Tue + Thu 16:00 UTC · Conversation pairs", announcement: "New Hindi–English and Spanish–English pairs forming this week." },
  { id: "techhelp", name: "Tech Help Circle", blurb: "Patient phone and app help", members: 73, meetup: "Mon 15:00 UTC · Fix-it hour", announcement: "Monday's fix-it hour covers phone storage full warnings." },
];

export const SEED_MESSAGES: Record<string, DemoMessage[]> = {
  reading: [
    { id: "reading-1", author: "Meera", text: "Finished the first story last night — the ending surprised me!", mine: false },
    { id: "reading-2", author: "Jonas", text: "I'm only halfway through. No spoilers until Saturday, please.", mine: false },
  ],
  cooking: [
    { id: "cooking-1", author: "Amara", text: "Made the lentil soup with extra ginger. Wonderful.", mine: false },
    { id: "cooking-2", author: "Ravi", text: "Mine came out too thick — does anyone add water halfway?", mine: false },
  ],
  walks: [
    { id: "walks-1", author: "Grace", text: "Beautiful fog on the lake path this morning.", mine: false },
    { id: "walks-2", author: "Tom", text: "I'll be at the park gate at 6:30 with the spare raincoats.", mine: false },
  ],
  music: [
    { id: "music-1", author: "Jonas", text: "Sharing a lullaby my grandmother used to hum.", mine: false },
    { id: "music-2", author: "Meera", text: "Friday's theme made me call my mother. Thank you all.", mine: false },
  ],
  gardening: [
    { id: "gardening-1", author: "Amara", text: "My balcony tomatoes finally have flowers!", mine: false },
    { id: "gardening-2", author: "Grace", text: "Reminder: the seed swap table opens at 9 sharp on Sunday.", mine: false },
  ],
  movies: [
    { id: "movies-1", author: "Ravi", text: "Voted for the train comedy. Short and sweet.", mine: false },
    { id: "movies-2", author: "Tom", text: "Last week's film discussion was the best one yet.", mine: false },
  ],
  language: [
    { id: "language-1", author: "Meera", text: "Practised ordering food in Spanish today. Small win!", mine: false },
    { id: "language-2", author: "Jonas", text: "Looking for a Hindi partner — I can offer German in return.", mine: false },
  ],
  techhelp: [
    { id: "techhelp-1", author: "Grace", text: "The storage tip worked — deleted old videos, phone is fast again.", mine: false },
    { id: "techhelp-2", author: "Tom", text: "Can someone explain app permissions in simple words on Monday?", mine: false },
  ],
};

export const PARTNERS: DemoProfile[] = [
  { id: "meera", name: "Meera", interests: ["Cooking", "Gardening"], languages: ["Hindi", "English"], note: "Loves swapping recipes and balcony tips.", greeting: "Hello! I heard you like cooking too. What's your favourite thing to make?" },
  { id: "jonas", name: "Jonas", interests: ["Books", "Travel"], languages: ["English", "German"], note: "Always reading something; happy to exchange languages.", greeting: "Hi there! Currently reading a travel memoir. What are you reading?" },
  { id: "amara", name: "Amara", interests: ["Music", "Languages"], languages: ["English", "French"], note: "Collects lullabies from everywhere.", greeting: "Hello! Do you have a song that reminds you of home?" },
  { id: "ravi", name: "Ravi", interests: ["Fitness", "Technology"], languages: ["English", "Hindi"], note: "Morning walker; glad to help with phones.", greeting: "Hi! Walking tomorrow morning? We start slow, I promise." },
];

export const VOLUNTEERS: DemoVolunteer[] = [
  { id: "sara", name: "Sara K.", skills: ["Technology", "Forms & letters"], languages: ["English", "Hindi"], availability: "Weekday evenings", about: "Retired teacher, patient with beginners and small screens." },
  { id: "daniel", name: "Daniel M.", skills: ["Errands", "Companionship"], languages: ["English"], availability: "Weekend mornings", about: "Neighbourhood volunteer for shopping trips and friendly visits." },
  { id: "priya", name: "Priya S.", skills: ["Language practice", "Reading help"], languages: ["Hindi", "English"], availability: "Tue and Thu afternoons", about: "Enjoys reading aloud and gentle conversation practice." },
  { id: "tom", name: "Tom H.", skills: ["Technology"], languages: ["English", "German"], availability: "Monday fix-it hour", about: "Helps with phones, apps, and video calls to family." },
  { id: "grace", name: "Grace N.", skills: ["Companionship", "Errands"], languages: ["English", "French"], availability: "Sunday afternoons", about: "Happy to accompany walks, appointments, and outings." },
];
