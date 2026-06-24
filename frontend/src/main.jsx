import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import './index.css';
import Login from './Login.jsx';
import Feed from './Feed.jsx';
import History from './History.jsx';
import Status from './Status.jsx';
import Learning from './Learning.jsx';
import Layout from './Layout.jsx';
import RequireAuth from './RequireAuth.jsx';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth><Layout /></RequireAuth>}>
          <Route path="/" element={<Navigate to="/feed" replace />} />
          <Route path="/feed" element={<Feed />} />
          <Route path="/feed/:date" element={<Feed />} />
          <Route path="/clip/:id" element={<Feed />} />
          <Route path="/history" element={<History />} />
          <Route path="/status" element={<Status />} />
          <Route path="/learning" element={<Learning />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
